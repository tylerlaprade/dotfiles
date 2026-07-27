"""Tests for incremental indexing.

One property matters more than the rest: an index built up a piece at a time
must hold exactly what an index built in one pass holds. Everything else here
is a way for that property to break.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from support import (
    Corpus,
    assert_matches_full_rebuild,
    claude_entry,
    codex_entry,
    codex_meta,
    connect,
    contents,
    grok_entry,
    index,
    pointed_at,
    recall,
)

CODEX_UUID = "019dff1d-385c-7822-8302-008a34dca659"
GROK_UUID = "019f7075-b809-7640-8c04-0575872411ca"


class IndexingCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "incremental.db")
        self.rebuild_db = str(self.tmp / "rebuild.db")

    def index(self, force=False):
        return index(self.corpus, self.db, force=force)

    def assert_matches_rebuild(self):
        assert_matches_full_rebuild(self, self.corpus, self.db, self.rebuild_db)

    def session_row(self, file_path):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT session_id, byte_offset, tail_hash, parser_version, "
                "project, slug, timestamp FROM sessions WHERE file_path = ?",
                (str(file_path),),
            ).fetchone()
        finally:
            conn.close()

    def texts(self, session_id):
        conn = sqlite3.connect(self.db)
        try:
            return [row[0] for row in conn.execute(
                "SELECT text FROM messages WHERE session_id = ?", (session_id,))]
        finally:
            conn.close()


class GrowingSessions(IndexingCase):
    def test_matches_a_full_rebuild_across_all_three_sources(self):
        claude = self.corpus.claude_session("11111111-1111-1111-1111-111111111111", [
            claude_entry("first claude turn", ts="2026-01-01T00:00:00.000Z"),
            claude_entry("first claude reply", role="assistant"),
        ])
        codex = self.corpus.codex_session(CODEX_UUID, [
            codex_meta(CODEX_UUID),
            codex_entry("first codex turn"),
        ])
        grok = self.corpus.grok_session(GROK_UUID, [grok_entry("first grok turn")])
        self.index()

        for round_no in range(2, 5):
            self.corpus.write(claude, [claude_entry(f"claude turn {round_no}")])
            self.corpus.write(codex, [codex_entry(f"codex turn {round_no}")])
            self.corpus.write(grok, [grok_entry(f"grok turn {round_no}")])
            self.index()

        self.assert_matches_rebuild()

    def test_a_growing_session_gains_messages_without_duplicating_them(self):
        path = self.corpus.claude_session("22222222-2222-2222-2222-222222222222", [
            claude_entry("alpha"), claude_entry("bravo")])
        self.index()
        self.corpus.write(path, [claude_entry("charlie")])
        self.index()
        texts = self.texts(self.session_row(path)[0])
        self.assertEqual(sorted(texts), ["alpha", "bravo", "charlie"])

    def test_an_unchanged_file_is_not_read_again(self):
        self.corpus.claude_session("33333333-3333-3333-3333-333333333333",
                                   [claude_entry("only turn")])
        self.assertEqual(self.index(), 1)
        self.assertEqual(self.index(), 0)

    def test_claude_and_codex_store_a_resume_point(self):
        claude = self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                            [claude_entry("turn")])
        codex = self.corpus.codex_session(CODEX_UUID,
                                          [codex_meta(CODEX_UUID), codex_entry("turn")])
        self.index()
        for path in (claude, codex):
            _, offset, tail_hash, version, *_ = self.session_row(path)
            self.assertEqual(offset, os.path.getsize(path))
            self.assertIsNotNone(tail_hash)
            self.assertEqual(version, recall.PARSER_VERSION)


class PartialLines(IndexingCase):
    def test_a_half_written_line_is_picked_up_once_it_completes(self):
        path = self.corpus.claude_session("55555555-5555-5555-5555-555555555555",
                                          [claude_entry("complete turn")])
        self.index()
        head = json.dumps(claude_entry("torn turn"))
        self.corpus.write_raw(path, head[: len(head) // 2])
        self.index()
        self.assertEqual(self.texts(self.session_row(path)[0]), ["complete turn"])

        self.corpus.write_raw(path, head[len(head) // 2:] + "\n")
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path)[0])),
                         ["complete turn", "torn turn"])
        self.assert_matches_rebuild()


class Mutations(IndexingCase):
    """Session files are not always appended to. Each of these must be noticed
    and force the file to be read again, or the index quietly goes wrong."""

    def claude_lines(self, count, name="66666666-6666-6666-6666-666666666666"):
        return self.corpus.claude_session(
            name, [claude_entry(f"turn {i}") for i in range(count)])

    def test_a_message_removed_from_the_middle_is_noticed(self):
        path = self.claude_lines(6)
        self.index()
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[2]
        self.corpus.write_raw(path, "".join(lines), mode="w")
        self.index()
        self.assertNotIn("turn 2", self.texts(self.session_row(path)[0]))
        self.assert_matches_rebuild()

    def test_a_removal_hidden_by_later_appends_is_noticed(self):
        """The file ends up longer than the stored offset again, so nothing but
        the tail hash can tell that the bytes underneath it moved."""
        path = self.claude_lines(6)
        self.index()
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[2]
        self.corpus.write_raw(path, "".join(lines), mode="w")
        self.corpus.write(path, [claude_entry(f"turn {i}") for i in range(6, 12)])
        self.index()
        texts = self.texts(self.session_row(path)[0])
        self.assertNotIn("turn 2", texts)
        self.assertIn("turn 11", texts)
        self.assert_matches_rebuild()

    def test_truncation_is_noticed(self):
        path = self.claude_lines(6)
        self.index()
        self.corpus.write(path, [claude_entry("turn 0")], mode="w")
        self.index()
        self.assertEqual(self.texts(self.session_row(path)[0]), ["turn 0"])
        self.assert_matches_rebuild()

    def test_replacement_by_rename_is_noticed(self):
        path = self.claude_lines(6)
        self.index()
        replacement = self.tmp / "replacement.jsonl"
        replacement.write_text(
            "".join(json.dumps(claude_entry(f"fresh {i}")) + "\n" for i in range(3)),
            encoding="utf-8")
        os.replace(replacement, path)
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path)[0])),
                         ["fresh 0", "fresh 1", "fresh 2"])
        self.assert_matches_rebuild()

    def test_an_edit_inside_the_tail_window_is_noticed(self):
        path = self.claude_lines(4)
        self.index()
        text = Path(path).read_text(encoding="utf-8").replace("turn 3", "edited 3")
        self.corpus.write_raw(path, text, mode="w")
        self.index()
        self.assertIn("edited 3", self.texts(self.session_row(path)[0]))
        self.assert_matches_rebuild()


class GrokIsNeverResumed(IndexingCase):
    """Grok rewrites the whole of chat_history.jsonl through a temp file every
    time it saves, so a byte offset into one means nothing."""

    def test_grok_rows_never_carry_a_resume_point(self):
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("one"), grok_entry("two")])
        self.index()
        self.assertEqual(self.session_row(path)[1], 0)
        self.corpus.write(path, [grok_entry("three")])
        self.index()
        self.assertEqual(self.session_row(path)[1], 0)

    def test_a_rewritten_grok_history_is_reread_in_full(self):
        path = self.corpus.grok_session(
            GROK_UUID, [grok_entry("kept"), grok_entry("dropped later")])
        self.index()
        replacement = self.tmp / "history.tmp"
        replacement.write_text(json.dumps(grok_entry("kept")) + "\n", encoding="utf-8")
        os.replace(replacement, path)
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(self.texts(self.session_row(path)[0]), ["kept"])
        self.assert_matches_rebuild()


class SessionIdCollisions(IndexingCase):
    """Session ids come from the file name, and workflow journals are all named
    journal.jsonl. Sharing one row would mean sharing one resume point."""

    def test_two_files_with_the_same_name_keep_their_own_messages(self):
        first = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-a" / "journal.jsonl",
            [claude_entry("from run a")])
        second = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-b" / "journal.jsonl",
            [claude_entry("from run b")])
        self.index()

        first_id, second_id = self.session_row(first)[0], self.session_row(second)[0]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.texts(first_id), ["from run a"])
        self.assertEqual(self.texts(second_id), ["from run b"])
        self.assert_matches_rebuild()

    def test_each_keeps_growing_independently(self):
        first = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-a" / "journal.jsonl",
            [claude_entry("a one")])
        second = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-b" / "journal.jsonl",
            [claude_entry("b one")])
        self.index()
        self.corpus.write(first, [claude_entry("a two")])
        self.corpus.write(second, [claude_entry("b two")])
        self.index()

        self.assertEqual(sorted(self.texts(self.session_row(first)[0])), ["a one", "a two"])
        self.assertEqual(sorted(self.texts(self.session_row(second)[0])), ["b one", "b two"])
        self.assert_matches_rebuild()

    def test_a_moved_file_inherits_its_id_instead_of_duplicating(self):
        old = self.corpus.claude_session("77777777-7777-7777-7777-777777777777",
                                         [claude_entry("carried over")], project="before")
        self.index()
        new = self.corpus.claude / "after" / "77777777-7777-7777-7777-777777777777.jsonl"
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
        self.corpus.stamp(new)
        self.index()

        sessions, _ = contents(self.db)
        self.assertNotIn(str(old), sessions)
        self.assertEqual(self.texts(self.session_row(new)[0]), ["carried over"])


class MetadataOnTheIncrementalPath(IndexingCase):
    """A tail read sees only the end of the file, so whatever the head supplied
    has to survive in the row rather than being overwritten with nothing."""

    def test_the_earliest_timestamp_survives_later_writes(self):
        path = self.corpus.claude_session("88888888-8888-8888-8888-888888888888", [
            claude_entry("opening", ts="2026-01-01T00:00:00.000Z")])
        self.index()
        first = self.session_row(path)[6]
        self.corpus.write(path, [claude_entry("later", ts="2026-06-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path)[6], first)

    def test_a_slug_seen_only_at_the_head_is_kept(self):
        path = self.corpus.claude_session("99999999-9999-9999-9999-999999999999", [
            claude_entry("opening", slug="the-real-slug")])
        self.index()
        self.corpus.write(path, [claude_entry("later with no slug")])
        self.index()
        self.assertEqual(self.session_row(path)[5], "the-real-slug")

    def test_a_slug_that_arrives_late_is_still_picked_up(self):
        """Claude writes its generated title after the session has run, often
        after the session has already been indexed once."""
        path = self.corpus.claude_session("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                          [claude_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path)[5], "")
        self.corpus.write(path, [claude_entry("later", slug="named-afterwards")])
        self.index()
        self.assertEqual(self.session_row(path)[5], "named-afterwards")
        self.assert_matches_rebuild()

    def test_the_first_slug_wins_over_a_later_one(self):
        """A full read keeps the first title it finds, so a tail read must not
        quietly replace it with a different one."""
        path = self.corpus.claude_session("ffffffff-ffff-ffff-ffff-ffffffffffff",
                                          [claude_entry("opening", slug="first-title")])
        self.index()
        self.corpus.write(path, [claude_entry("later", slug="second-title")])
        self.index()
        self.assertEqual(self.session_row(path)[5], "first-title")
        self.assert_matches_rebuild()

    def test_the_first_working_directory_wins(self):
        """A session can change directory part way through — /add-dir, or a
        resume somewhere else. A full read keeps the first one."""
        path = self.corpus.claude_session("10101010-1010-1010-1010-101010101010",
                                          [claude_entry("opening", cwd="/first/place")])
        self.index()
        self.corpus.write(path, [claude_entry("later", cwd="/second/place")])
        self.index()
        self.assertEqual(self.session_row(path)[4], "/first/place")
        self.assert_matches_rebuild()

    def test_a_timestamp_earlier_than_the_stored_one_still_wins(self):
        """Forked and compacted transcripts can carry entries out of order. The
        stored timestamp is the earliest anywhere in the file, not the earliest
        seen so far."""
        path = self.corpus.claude_session("20202020-2020-2020-2020-202020202020",
                                          [claude_entry("opening", ts="2026-06-01T00:00:00.000Z")])
        self.index()
        self.corpus.write(path, [claude_entry("older", ts="2026-01-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path)[6],
                         recall.parse_iso_timestamp("2026-01-01T00:00:00.000Z"))
        self.assert_matches_rebuild()

    def test_a_timestamp_arriving_after_none_at_all(self):
        path = self.corpus.claude_session("30303030-3030-3030-3030-303030303030",
                                          [claude_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path)[6], 0)
        self.corpus.write(path, [claude_entry("later", ts="2026-03-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path)[6],
                         recall.parse_iso_timestamp("2026-03-01T00:00:00.000Z"))
        self.assert_matches_rebuild()

    def test_codex_keeps_the_id_from_its_first_line(self):
        """Codex puts the real session id in session_meta at the head, so a
        tail read must not fall back to the rollout file name."""
        path = self.corpus.codex_session(CODEX_UUID,
                                         [codex_meta(CODEX_UUID), codex_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path)[0], CODEX_UUID)
        self.corpus.write(path, [codex_entry("later")])
        self.index()
        self.assertEqual(self.session_row(path)[0], CODEX_UUID)
        self.assertEqual(sorted(self.texts(CODEX_UUID)), ["later", "opening"])

    def test_a_grok_title_written_after_the_first_index_is_picked_up(self):
        directory = self.corpus.grok / "%2Fwork%2Fproject" / GROK_UUID
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("opening")])
        self.index()
        (directory / "summary.json").write_text(
            json.dumps({"info": {"cwd": "/work/project"},
                        "generated_title": "titled afterwards"}), encoding="utf-8")
        self.corpus.write(path, [grok_entry("later")])
        self.index()
        self.assertEqual(self.session_row(path)[5], "titled afterwards")


class Reindex(IndexingCase):
    def test_reindex_rebuilds_without_duplicating(self):
        path = self.corpus.claude_session("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                                          [claude_entry("one"), claude_entry("two")])
        self.index()
        self.corpus.write(path, [claude_entry("three")])
        self.index()
        before = contents(self.db)
        self.index(force=True)
        self.assertEqual(contents(self.db), before)

    def test_reindex_restores_resume_points(self):
        path = self.corpus.claude_session("cccccccc-cccc-cccc-cccc-cccccccccccc",
                                          [claude_entry("one")])
        self.index(force=True)
        _, offset, tail_hash, version, *_ = self.session_row(path)
        self.assertEqual(offset, os.path.getsize(path))
        self.assertIsNotNone(tail_hash)
        self.assertEqual(version, recall.PARSER_VERSION)

    def test_automerge_is_left_switched_on(self):
        """It is turned off for the bulk insert. Leaving it off would let FTS5
        segments pile up until someone noticed searches getting slower."""
        self.corpus.claude_session("dddddddd-dddd-dddd-dddd-dddddddddddd",
                                   [claude_entry("one")])
        self.index()
        conn = sqlite3.connect(self.db)
        try:
            value = conn.execute(
                "SELECT v FROM messages_config WHERE k = 'automerge'").fetchone()
        finally:
            conn.close()
        self.assertEqual(value[0], 4)


class VanishedFiles(IndexingCase):
    """Tools age out their own transcripts. Nearly half the sessions in a
    working index point at files that are gone, and the index is the only place
    those conversations still exist."""

    def test_a_deleted_file_keeps_its_messages(self):
        path = self.corpus.claude_session("40404040-4040-4040-4040-404040404040",
                                          [claude_entry("worth keeping")])
        self.corpus.claude_session("41414141-4141-4141-4141-414141414141",
                                   [claude_entry("still here")])
        self.index()
        session_id = self.session_row(path)[0]
        os.remove(path)
        self.index()
        self.assertEqual(self.texts(session_id), ["worth keeping"])

    def test_reindex_keeps_them_too(self):
        """A rebuild re-reads what it can. It is not an instruction to forget
        everything it cannot."""
        path = self.corpus.claude_session("42424242-4242-4242-4242-424242424242",
                                          [claude_entry("worth keeping")])
        self.corpus.claude_session("43434343-4343-4343-4343-434343434343",
                                   [claude_entry("still here")])
        self.index()
        session_id = self.session_row(path)[0]
        os.remove(path)
        self.index(force=True)
        self.assertEqual(self.texts(session_id), ["worth keeping"])
        self.assertEqual(len(contents(self.db)[0]), 2)

    def test_an_unreadable_file_keeps_what_was_already_indexed(self):
        """A permission error, or a transcript rotated away mid-scan, must cost
        nothing that is already in the index."""
        path = self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                          [claude_entry("indexed before the error")])
        self.index()
        session_id = self.session_row(path)[0]

        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        self.corpus.stamp(path)
        with redirect_stderr(io.StringIO()):
            self.index()
        self.assertEqual(self.texts(session_id), ["indexed before the error"])


class InterruptedRebuild(IndexingCase):
    def test_a_rebuild_that_dies_part_way_leaves_the_index_intact(self):
        """The deletes belong to the run's transaction. Committing them first
        would leave an empty index behind for as long as the rebuild takes,
        and for good if it never finishes."""
        self.corpus.claude_session("50505050-5050-5050-5050-505050505050",
                                   [claude_entry("survives")])
        self.index()
        before = contents(self.db)

        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                original = recall.parse_session

                def explode(path, source, start=0):
                    raise KeyboardInterrupt("killed mid-rebuild")

                recall.parse_session = explode
                with self.assertRaises(KeyboardInterrupt):
                    recall.index_sessions(conn, force=True)
                conn.rollback()
            finally:
                recall.parse_session = original
                conn.close()

        self.assertEqual(contents(self.db), before)


class MalformedInput(IndexingCase):
    def test_a_timestamp_that_is_not_a_number_costs_one_line(self):
        """Not the whole run. An uncaught error here would discard every
        session parsed before it, since nothing is committed until the end."""
        path = self.corpus.claude_session("60606060-6060-6060-6060-606060606060",
                                          [claude_entry("good line")])
        self.corpus.write_raw(
            path, '{"type":"user","timestamp":Infinity,"message":{"content":"bad line"}}\n')
        self.index()
        self.assertIn("good line", self.texts(self.session_row(path)[0]))

    def test_parse_iso_timestamp_survives_anything(self):
        for value in (float("inf"), float("nan"), "not a date", "", None, [], {}, True):
            with self.subTest(value=value):
                recall.parse_iso_timestamp(value)


class ParserVersion(IndexingCase):
    def test_bumping_the_parser_version_forces_a_full_reread(self):
        path = self.corpus.claude_session("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                                          [claude_entry("one")])
        self.index()
        self.corpus.write(path, [claude_entry("two")])

        original = recall.PARSER_VERSION
        recall.PARSER_VERSION = original + 1
        try:
            self.index()
            self.assertEqual(self.session_row(path)[3], original + 1)
            self.assertEqual(sorted(self.texts(self.session_row(path)[0])), ["one", "two"])
        finally:
            recall.PARSER_VERSION = original

    def test_a_bump_reaches_a_session_that_has_stopped_growing(self):
        """The point of the version is that a change to what the parsers keep
        applies to sessions already indexed. Skipping on mtime alone would
        leave every finished session parsed the old way for good."""
        path = self.corpus.claude_session("70707070-7070-7070-7070-707070707070",
                                          [claude_entry("written once")])
        self.index()
        self.assertEqual(self.index(), 0)

        original = recall.PARSER_VERSION
        recall.PARSER_VERSION = original + 1
        try:
            self.assertEqual(self.index(), 1)
            self.assertEqual(self.session_row(path)[3], original + 1)
            self.assertEqual(self.texts(self.session_row(path)[0]), ["written once"])
        finally:
            recall.PARSER_VERSION = original


if __name__ == "__main__":
    unittest.main()


class NothingIsDeletedBeforeItIsReadAgain(IndexingCase):
    """A rebuild that deletes up front loses any session that stops being
    readable while it runs — and half of them have no file to re-read at all."""

    def test_reindex_keeps_a_session_that_becomes_unreadable_mid_run(self):
        path = self.corpus.claude_session("80808080-8080-8080-8080-808080808080",
                                          [claude_entry("must survive")])
        self.corpus.claude_session("81818181-8181-8181-8181-818181818181",
                                   [claude_entry("also here")])
        self.index()
        session_id = self.session_row(path)[0]

        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        with redirect_stderr(io.StringIO()):
            self.index(force=True)
        self.assertEqual(self.texts(session_id), ["must survive"])

    def test_reindex_keeps_a_session_deleted_mid_run(self):
        path = self.corpus.claude_session("82828282-8282-8282-8282-828282828282",
                                          [claude_entry("must survive")])
        self.index()
        session_id = self.session_row(path)[0]
        os.remove(path)
        self.index(force=True)
        self.assertEqual(self.texts(session_id), ["must survive"])


class AnIdIsNeverTakenFromASessionThatSurvives(IndexingCase):
    def test_a_colliding_file_does_not_delete_the_holder(self):
        """Two workflow journals share a derived id. When the one holding the
        bare id ages out, the other must not inherit it by deleting it."""
        first = self.corpus.write(
            self.corpus.claude / "proj" / "run-a" / "journal.jsonl",
            [claude_entry("from run a")])
        second = self.corpus.write(
            self.corpus.claude / "proj" / "run-b" / "journal.jsonl",
            [claude_entry("from run b")])
        self.index()
        kept = {self.session_row(first)[0]: "from run a",
                self.session_row(second)[0]: "from run b"}

        # Whichever holds the bare id, delete its file and re-read the other.
        bare = next(sid for sid in kept if "@" not in sid)
        gone = first if self.session_row(first)[0] == bare else second
        survivor = second if gone is first else first
        os.remove(gone)
        self.corpus.write(survivor, [claude_entry("a later turn")])
        self.index()

        self.assertEqual(self.texts(bare), [kept[bare]])


class MalformedJson(IndexingCase):
    def test_a_json_line_that_is_not_an_object_costs_one_line(self):
        path = self.corpus.claude_session("83838383-8383-8383-8383-838383838383",
                                          [claude_entry("good line")])
        self.corpus.write_raw(path, "[1, 2, 3]\n42\n\"a bare string\"\nnull\n")
        self.corpus.write(path, [claude_entry("after the junk")])
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path)[0])),
                         ["after the junk", "good line"])

    def test_a_grok_summary_that_is_not_an_object_is_ignored(self):
        directory = self.corpus.grok / "%2Fwork%2Fproject" / GROK_UUID
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")])
        (directory / "summary.json").write_text("[1, 2, 3]", encoding="utf-8")
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(self.texts(self.session_row(path)[0]), ["a turn"])


class RebuildingTheMessageIndexIsAllOrNothing(IndexingCase):
    def test_an_interrupted_rebuild_leaves_the_index_usable(self):
        """It used to commit each statement as it went, so a run killed part
        way through left a half-built table that every later run died on."""
        self.corpus.claude_session("84848484-8484-8484-8484-848484848484",
                                   [claude_entry("still findable")])
        self.index()

        conn = sqlite3.connect(self.db)
        try:
            conn.executescript("""
                DROP TABLE messages;
                CREATE VIRTUAL TABLE messages USING fts5(
                    session_id UNINDEXED, role, text, tokenize='porter unicode61');
                INSERT INTO messages VALUES ('s', 'user', 'still findable');
            """)
            conn.commit()

            class DiesPartWay:
                """Stands in for the process being killed mid-rebuild."""

                def __init__(self, wrapped):
                    self._wrapped = wrapped

                def execute(self, sql, *args):
                    if sql.strip().startswith("DROP TABLE messages"):
                        raise KeyboardInterrupt("killed mid-rebuild")
                    return self._wrapped.execute(sql, *args)

                def __getattr__(self, name):
                    return getattr(self._wrapped, name)

            with redirect_stderr(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    recall.migrate_message_columns(DiesPartWay(conn))

            leftovers = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'messages_rebuilt'")]
            self.assertEqual(leftovers, [])
            self.assertEqual(conn.execute("SELECT text FROM messages").fetchone()[0],
                             "still findable")

            # And the next run completes it.
            with redirect_stderr(io.StringIO()):
                recall.migrate_message_columns(conn)
            self.assertIn("role UNINDEXED", conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0])
            self.assertEqual(conn.execute("SELECT text FROM messages").fetchone()[0],
                             "still findable")
        finally:
            conn.close()
