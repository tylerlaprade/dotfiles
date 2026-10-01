"""Tests for incremental indexing.

One property matters more than the rest: an index built up a piece at a time
must hold exactly what an index built in one pass holds. Everything else here
is a way for that property to break.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import override
from unittest import mock

import recall
from recall import Indexed, JSONValue, SqlValue, fetch_all, fetch_one, sql_text
from support import (
    Corpus,
    assert_matches_full_rebuild,
    claude_entry,
    claude_noise,
    codex_entry,
    codex_meta,
    connect,
    contents,
    grok_entry,
    grok_noise,
    index,
    pointed_at,
)

CODEX_UUID = "019dff1d-385c-7822-8302-008a34dca659"
GROK_UUID = "019f7075-b809-7640-8c04-0575872411ca"


class PatchableConnection(sqlite3.Connection):
    """A connection whose methods a test can replace, which sqlite3's own cannot."""


def first_value(conn: sqlite3.Connection, sql: str) -> SqlValue:
    row = fetch_one(conn, sql)
    return row[0] if row else None


class IndexingCase(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.tmp: Path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corpus: Corpus = Corpus(self.tmp / "corpus")
        self.db: str = str(self.tmp / "incremental.db")
        self.rebuild_db: str = str(self.tmp / "rebuild.db")

    def index(self, *, force: bool = False) -> int:
        return index(self.corpus, self.db, force=force)

    def assert_matches_rebuild(self) -> None:
        assert_matches_full_rebuild(self, self.corpus, self.db, self.rebuild_db)

    def session_row(self, file_path: Path | str) -> Indexed:
        conn = sqlite3.connect(self.db)
        try:
            return recall.load_indexed_state(conn)[str(file_path)]
        finally:
            conn.close()

    def texts(self, session_id: str) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [sql_text(row[0]) for row in fetch_all(
                conn, "SELECT text FROM messages WHERE session_id = ?", (session_id,))]
        finally:
            conn.close()


class GrowingSessions(IndexingCase):
    def test_matches_a_full_rebuild_across_all_three_sources(self) -> None:
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

    def test_a_growing_session_gains_messages_without_duplicating_them(self) -> None:
        path = self.corpus.claude_session("22222222-2222-2222-2222-222222222222", [
            claude_entry("alpha"), claude_entry("bravo")])
        self.index()
        self.corpus.write(path, [claude_entry("charlie")])
        self.index()
        texts = self.texts(self.session_row(path).session_id)
        self.assertEqual(sorted(texts), ["alpha", "bravo", "charlie"])

    def test_an_unchanged_file_is_not_read_again(self) -> None:
        self.corpus.claude_session("33333333-3333-3333-3333-333333333333",
                                   [claude_entry("only turn")])
        self.assertEqual(self.index(), 1)
        self.assertEqual(self.index(), 0)

    def test_claude_and_codex_store_a_resume_point(self) -> None:
        claude = self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                            [claude_entry("turn")])
        codex = self.corpus.codex_session(CODEX_UUID,
                                          [codex_meta(CODEX_UUID), codex_entry("turn")])
        self.index()
        for path in (claude, codex):
            row = self.session_row(path)
            self.assertEqual(row.byte_offset, path.stat().st_size)
            self.assertIsNotNone(row.tail_hash)
            self.assertEqual(row.parser_version, recall.PARSER_VERSION)


class PartialLines(IndexingCase):
    def test_a_half_written_line_is_picked_up_once_it_completes(self) -> None:
        path = self.corpus.claude_session("55555555-5555-5555-5555-555555555555",
                                          [claude_entry("complete turn")])
        self.index()
        head = json.dumps(claude_entry("torn turn"))
        self.corpus.write_raw(path, head[: len(head) // 2])
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["complete turn"])

        self.corpus.write_raw(path, head[len(head) // 2:] + "\n")
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path).session_id)),
                         ["complete turn", "torn turn"])
        self.assert_matches_rebuild()


class Mutations(IndexingCase):
    """Session files are not always appended to. Each of these must be noticed
    and force the file to be read again, or the index quietly goes wrong."""

    def claude_lines(self, count: int, name: str = "66666666-6666-6666-6666-666666666666") -> Path:
        return self.corpus.claude_session(
            name, [claude_entry(f"turn {i}") for i in range(count)])

    def test_a_message_removed_from_the_middle_is_noticed(self) -> None:
        path = self.claude_lines(6)
        self.index()
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[2]
        self.corpus.write_raw(path, "".join(lines), mode="w")
        self.index()
        self.assertNotIn("turn 2", self.texts(self.session_row(path).session_id))
        self.assert_matches_rebuild()

    def test_a_removal_hidden_by_later_appends_is_noticed(self) -> None:
        """The file ends up longer than the stored offset again, so nothing but
        the tail hash can tell that the bytes underneath it moved."""
        path = self.claude_lines(6)
        self.index()
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[2]
        self.corpus.write_raw(path, "".join(lines), mode="w")
        self.corpus.write(path, [claude_entry(f"turn {i}") for i in range(6, 12)])
        self.index()
        texts = self.texts(self.session_row(path).session_id)
        self.assertNotIn("turn 2", texts)
        self.assertIn("turn 11", texts)
        self.assert_matches_rebuild()

    def test_truncation_is_noticed(self) -> None:
        path = self.claude_lines(6)
        self.index()
        self.corpus.write(path, [claude_entry("turn 0")], mode="w")
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["turn 0"])
        self.assert_matches_rebuild()

    def test_replacement_by_rename_is_noticed(self) -> None:
        path = self.claude_lines(6)
        self.index()
        replacement = self.tmp / "replacement.jsonl"
        replacement.write_text(
            "".join(json.dumps(claude_entry(f"fresh {i}")) + "\n" for i in range(3)),
            encoding="utf-8")
        replacement.replace(path)
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path).session_id)),
                         ["fresh 0", "fresh 1", "fresh 2"])
        self.assert_matches_rebuild()

    def test_an_edit_inside_the_tail_window_is_noticed(self) -> None:
        path = self.claude_lines(4)
        self.index()
        text = Path(path).read_text(encoding="utf-8").replace("turn 3", "edited 3")
        self.corpus.write_raw(path, text, mode="w")
        self.index()
        self.assertIn("edited 3", self.texts(self.session_row(path).session_id))
        self.assert_matches_rebuild()


class GrokIsNeverResumed(IndexingCase):
    """Grok rewrites the whole of chat_history.jsonl through a temp file every
    time it saves, so a byte offset into one means nothing."""

    def test_grok_rows_never_carry_a_resume_point(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("one"), grok_entry("two")])
        self.index()
        self.assertEqual(self.session_row(path).byte_offset, 0)
        self.corpus.write(path, [grok_entry("three")])
        self.index()
        self.assertEqual(self.session_row(path).byte_offset, 0)

    def test_a_rewritten_grok_history_is_reread_in_full(self) -> None:
        path = self.corpus.grok_session(
            GROK_UUID, [grok_entry("kept"), grok_entry("dropped later")])
        self.index()
        replacement = self.tmp / "history.tmp"
        replacement.write_text(json.dumps(grok_entry("kept")) + "\n", encoding="utf-8")
        replacement.replace(path)
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["kept"])
        self.assert_matches_rebuild()


class SessionIdCollisions(IndexingCase):
    """Session ids come from the file name, and workflow journals are all named
    journal.jsonl. Sharing one row would mean sharing one resume point."""

    def test_two_files_with_the_same_name_keep_their_own_messages(self) -> None:
        first = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-a" / "journal.jsonl",
            [claude_entry("from run a")])
        second = self.corpus.write(
            self.corpus.claude / "proj" / "workflows" / "run-b" / "journal.jsonl",
            [claude_entry("from run b")])
        self.index()

        first_id, second_id = self.session_row(first).session_id, self.session_row(second).session_id
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.texts(first_id), ["from run a"])
        self.assertEqual(self.texts(second_id), ["from run b"])
        self.assert_matches_rebuild()

    def test_each_keeps_growing_independently(self) -> None:
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

        self.assertEqual(sorted(self.texts(self.session_row(first).session_id)), ["a one", "a two"])
        self.assertEqual(sorted(self.texts(self.session_row(second).session_id)), ["b one", "b two"])
        self.assert_matches_rebuild()

    def test_a_moved_file_inherits_its_id_instead_of_duplicating(self) -> None:
        old = self.corpus.claude_session("77777777-7777-7777-7777-777777777777",
                                         [claude_entry("carried over")], project="before")
        self.index()
        new = self.corpus.claude / "after" / "77777777-7777-7777-7777-777777777777.jsonl"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.replace(new)
        self.corpus.stamp(new)
        self.index()

        sessions, _ = contents(self.db)
        self.assertNotIn(str(old), sessions)
        self.assertEqual(self.texts(self.session_row(new).session_id), ["carried over"])


class MetadataOnTheIncrementalPath(IndexingCase):
    """A tail read sees only the end of the file, so whatever the head supplied
    has to survive in the row rather than being overwritten with nothing."""

    def test_the_earliest_timestamp_survives_later_writes(self) -> None:
        path = self.corpus.claude_session("88888888-8888-8888-8888-888888888888", [
            claude_entry("opening", ts="2026-01-01T00:00:00.000Z")])
        self.index()
        first = self.session_row(path).timestamp
        self.corpus.write(path, [claude_entry("later", ts="2026-06-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path).timestamp, first)

    def test_a_slug_seen_only_at_the_head_is_kept(self) -> None:
        path = self.corpus.claude_session("99999999-9999-9999-9999-999999999999", [
            claude_entry("opening", slug="the-real-slug")])
        self.index()
        self.corpus.write(path, [claude_entry("later with no slug")])
        self.index()
        self.assertEqual(self.session_row(path).slug, "the-real-slug")

    def test_a_slug_that_arrives_late_is_still_picked_up(self) -> None:
        """Claude writes its generated title after the session has run, often
        after the session has already been indexed once."""
        path = self.corpus.claude_session("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                          [claude_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path).slug, "")
        self.corpus.write(path, [claude_entry("later", slug="named-afterwards")])
        self.index()
        self.assertEqual(self.session_row(path).slug, "named-afterwards")
        self.assert_matches_rebuild()

    def test_the_first_slug_wins_over_a_later_one(self) -> None:
        """A full read keeps the first title it finds, so a tail read must not
        quietly replace it with a different one."""
        path = self.corpus.claude_session("ffffffff-ffff-ffff-ffff-ffffffffffff",
                                          [claude_entry("opening", slug="first-title")])
        self.index()
        self.corpus.write(path, [claude_entry("later", slug="second-title")])
        self.index()
        self.assertEqual(self.session_row(path).slug, "first-title")
        self.assert_matches_rebuild()

    def test_the_first_working_directory_wins(self) -> None:
        """A session can change directory part way through — /add-dir, or a
        resume somewhere else. A full read keeps the first one."""
        path = self.corpus.claude_session("10101010-1010-1010-1010-101010101010",
                                          [claude_entry("opening", cwd="/first/place")])
        self.index()
        self.corpus.write(path, [claude_entry("later", cwd="/second/place")])
        self.index()
        self.assertEqual(self.session_row(path).project, "/first/place")
        self.assert_matches_rebuild()

    def test_a_timestamp_earlier_than_the_stored_one_still_wins(self) -> None:
        """Forked and compacted transcripts can carry entries out of order. The
        stored timestamp is the earliest anywhere in the file, not the earliest
        seen so far."""
        path = self.corpus.claude_session("20202020-2020-2020-2020-202020202020",
                                          [claude_entry("opening", ts="2026-06-01T00:00:00.000Z")])
        self.index()
        self.corpus.write(path, [claude_entry("older", ts="2026-01-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path).timestamp,
                         recall.parse_iso_timestamp("2026-01-01T00:00:00.000Z"))
        self.assert_matches_rebuild()

    def test_a_timestamp_arriving_after_none_at_all(self) -> None:
        path = self.corpus.claude_session("30303030-3030-3030-3030-303030303030",
                                          [claude_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path).timestamp, 0)
        self.corpus.write(path, [claude_entry("later", ts="2026-03-01T00:00:00.000Z")])
        self.index()
        self.assertEqual(self.session_row(path).timestamp,
                         recall.parse_iso_timestamp("2026-03-01T00:00:00.000Z"))
        self.assert_matches_rebuild()

    def test_codex_keeps_the_id_from_its_first_line(self) -> None:
        """Codex puts the real session id in session_meta at the head, so a
        tail read must not fall back to the rollout file name."""
        path = self.corpus.codex_session(CODEX_UUID,
                                         [codex_meta(CODEX_UUID), codex_entry("opening")])
        self.index()
        self.assertEqual(self.session_row(path).session_id, CODEX_UUID)
        self.corpus.write(path, [codex_entry("later")])
        self.index()
        self.assertEqual(self.session_row(path).session_id, CODEX_UUID)
        self.assertEqual(sorted(self.texts(CODEX_UUID)), ["later", "opening"])

    def test_a_grok_title_written_after_the_first_index_is_picked_up(self) -> None:
        directory = self.corpus.grok / "%2Fwork%2Fproject" / GROK_UUID
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("opening")])
        self.index()
        (directory / "summary.json").write_text(
            json.dumps({"info": {"cwd": "/work/project"},
                        "generated_title": "titled afterwards"}), encoding="utf-8")
        self.corpus.write(path, [grok_entry("later")])
        self.index()
        self.assertEqual(self.session_row(path).slug, "titled afterwards")


class Reindex(IndexingCase):
    def test_reindex_rebuilds_without_duplicating(self) -> None:
        path = self.corpus.claude_session("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                                          [claude_entry("one"), claude_entry("two")])
        self.index()
        self.corpus.write(path, [claude_entry("three")])
        self.index()
        before = contents(self.db)
        self.index(force=True)
        self.assertEqual(contents(self.db), before)

    def test_reindex_restores_resume_points(self) -> None:
        path = self.corpus.claude_session("cccccccc-cccc-cccc-cccc-cccccccccccc",
                                          [claude_entry("one")])
        self.index(force=True)
        row = self.session_row(path)
        self.assertEqual(row.byte_offset, path.stat().st_size)
        self.assertIsNotNone(row.tail_hash)
        self.assertEqual(row.parser_version, recall.PARSER_VERSION)

    def test_automerge_is_left_switched_on(self) -> None:
        """It is turned off for the bulk insert. Leaving it off would let FTS5
        segments pile up until someone noticed searches getting slower."""
        self.corpus.claude_session("dddddddd-dddd-dddd-dddd-dddddddddddd",
                                   [claude_entry("one")])
        self.index()
        conn = sqlite3.connect(self.db)
        try:
            value = fetch_one(conn, "SELECT v FROM messages_config WHERE k = 'automerge'")
        finally:
            conn.close()
        self.assertEqual(value, (4,))


class VanishedFiles(IndexingCase):
    """Tools age out their own transcripts. Nearly half the sessions in a
    working index point at files that are gone, and the index is the only place
    those conversations still exist."""

    def test_a_deleted_file_keeps_its_messages(self) -> None:
        path = self.corpus.claude_session("40404040-4040-4040-4040-404040404040",
                                          [claude_entry("worth keeping")])
        self.corpus.claude_session("41414141-4141-4141-4141-414141414141",
                                   [claude_entry("still here")])
        self.index()
        session_id = self.session_row(path).session_id
        path.unlink()
        self.index()
        self.assertEqual(self.texts(session_id), ["worth keeping"])

    def test_reindex_keeps_them_too(self) -> None:
        """A rebuild re-reads what it can. It is not an instruction to forget
        everything it cannot."""
        path = self.corpus.claude_session("42424242-4242-4242-4242-424242424242",
                                          [claude_entry("worth keeping")])
        self.corpus.claude_session("43434343-4343-4343-4343-434343434343",
                                   [claude_entry("still here")])
        self.index()
        session_id = self.session_row(path).session_id
        path.unlink()
        self.index(force=True)
        self.assertEqual(self.texts(session_id), ["worth keeping"])
        self.assertEqual(len(contents(self.db)[0]), 2)

    def test_an_unreadable_file_keeps_what_was_already_indexed(self) -> None:
        """A permission error, or a transcript rotated away mid-scan, must cost
        nothing that is already in the index."""
        path = self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                          [claude_entry("indexed before the error")])
        self.index()
        session_id = self.session_row(path).session_id

        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        self.corpus.stamp(path)
        with redirect_stderr(io.StringIO()):
            self.index()
        self.assertEqual(self.texts(session_id), ["indexed before the error"])


class InterruptedRebuild(IndexingCase):
    def test_a_rebuild_that_dies_part_way_leaves_the_index_intact(self) -> None:
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
                with mock.patch.object(recall, "parse_session",
                                       side_effect=KeyboardInterrupt("killed mid-rebuild")), \
                        self.assertRaises(KeyboardInterrupt):
                    recall.index_sessions(conn, force=True)
                conn.rollback()
            finally:
                conn.close()

        self.assertEqual(contents(self.db), before)


class MalformedInput(IndexingCase):
    def test_a_timestamp_that_is_not_a_number_costs_one_line(self) -> None:
        """Not the whole run. An uncaught error here would discard every
        session parsed before it, since nothing is committed until the end."""
        path = self.corpus.claude_session("60606060-6060-6060-6060-606060606060",
                                          [claude_entry("good line")])
        self.corpus.write_raw(
            path, '{"type":"user","timestamp":Infinity,"message":{"content":"bad line"}}\n')
        self.index()
        self.assertIn("good line", self.texts(self.session_row(path).session_id))

    def test_parse_iso_timestamp_survives_anything(self) -> None:
        values: tuple[JSONValue, ...] = (float("inf"), float("nan"), "not a date", "", None, [], {}, True)
        for value in values:
            with self.subTest(value=value):
                recall.parse_iso_timestamp(value)


class ParserVersion(IndexingCase):
    def test_bumping_the_parser_version_forces_a_full_reread(self) -> None:
        path = self.corpus.claude_session("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                                          [claude_entry("one")])
        self.index()
        self.corpus.write(path, [claude_entry("two")])

        original = recall.PARSER_VERSION
        recall.PARSER_VERSION = original + 1
        try:
            self.index()
            self.assertEqual(self.session_row(path).parser_version, original + 1)
            self.assertEqual(sorted(self.texts(self.session_row(path).session_id)), ["one", "two"])
        finally:
            recall.PARSER_VERSION = original

    def test_a_bump_reaches_a_session_that_has_stopped_growing(self) -> None:
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
            self.assertEqual(self.session_row(path).parser_version, original + 1)
            self.assertEqual(self.texts(self.session_row(path).session_id), ["written once"])
        finally:
            recall.PARSER_VERSION = original


class NothingIsDeletedBeforeItIsReadAgain(IndexingCase):
    """A rebuild that deletes up front loses any session that stops being
    readable while it runs — and half of them have no file to re-read at all."""

    def test_reindex_keeps_a_session_that_becomes_unreadable_mid_run(self) -> None:
        path = self.corpus.claude_session("80808080-8080-8080-8080-808080808080",
                                          [claude_entry("must survive")])
        self.corpus.claude_session("81818181-8181-8181-8181-818181818181",
                                   [claude_entry("also here")])
        self.index()
        session_id = self.session_row(path).session_id

        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        with redirect_stderr(io.StringIO()):
            self.index(force=True)
        self.assertEqual(self.texts(session_id), ["must survive"])

    def test_reindex_keeps_a_session_deleted_mid_run(self) -> None:
        path = self.corpus.claude_session("82828282-8282-8282-8282-828282828282",
                                          [claude_entry("must survive")])
        self.index()
        session_id = self.session_row(path).session_id
        path.unlink()
        self.index(force=True)
        self.assertEqual(self.texts(session_id), ["must survive"])


class AnIdIsNeverTakenFromASessionThatSurvives(IndexingCase):
    def test_a_colliding_file_does_not_delete_the_holder(self) -> None:
        """Two workflow journals share a derived id. When the one holding the
        bare id ages out, the other must not inherit it by deleting it."""
        first = self.corpus.write(
            self.corpus.claude / "proj" / "run-a" / "journal.jsonl",
            [claude_entry("from run a")])
        second = self.corpus.write(
            self.corpus.claude / "proj" / "run-b" / "journal.jsonl",
            [claude_entry("from run b")])
        self.index()
        kept = {self.session_row(first).session_id: "from run a",
                self.session_row(second).session_id: "from run b"}

        # Whichever holds the bare id, delete its file and re-read the other.
        bare = next(sid for sid in kept if "@" not in sid)
        gone = first if self.session_row(first).session_id == bare else second
        survivor = second if gone is first else first
        gone.unlink()
        self.corpus.write(survivor, [claude_entry("a later turn")])
        self.index()

        self.assertEqual(self.texts(bare), [kept[bare]])


class MalformedJson(IndexingCase):
    def test_a_json_line_that_is_not_an_object_costs_one_line(self) -> None:
        path = self.corpus.claude_session("83838383-8383-8383-8383-838383838383",
                                          [claude_entry("good line")])
        self.corpus.write_raw(path, '[1, 2, 3]\n42\n"a bare string"\nnull\n')
        self.corpus.write(path, [claude_entry("after the junk")])
        self.index()
        self.assertEqual(sorted(self.texts(self.session_row(path).session_id)),
                         ["after the junk", "good line"])

    def test_a_grok_summary_that_is_not_an_object_is_ignored(self) -> None:
        directory = self.corpus.grok / "%2Fwork%2Fproject" / GROK_UUID
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")])
        (directory / "summary.json").write_text("[1, 2, 3]", encoding="utf-8")
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["a turn"])


class RebuildingTheMessageIndexIsAllOrNothing(IndexingCase):
    def test_an_interrupted_rebuild_leaves_the_index_usable(self) -> None:
        """It used to commit each statement as it went, so a run killed part
        way through left a half-built table that every later run died on."""
        self.corpus.claude_session("84848484-8484-8484-8484-848484848484",
                                   [claude_entry("still findable")])
        self.index()

        conn = sqlite3.connect(self.db, factory=PatchableConnection)
        try:
            conn.executescript("""
                DROP TABLE messages;
                CREATE VIRTUAL TABLE messages USING fts5(
                    session_id UNINDEXED, role, text, tokenize='porter unicode61');
                INSERT INTO messages VALUES ('s', 'user', 'still findable');
            """)
            conn.commit()

            execute = conn.execute

            def dies_part_way(sql: str, *parameters: tuple[SqlValue, ...]) -> sqlite3.Cursor:
                """Stands in for the process being killed mid-rebuild."""
                if sql.strip().startswith("DROP TABLE messages"):
                    raise KeyboardInterrupt("killed mid-rebuild")
                return execute(sql, *parameters)

            with redirect_stderr(io.StringIO()), \
                    mock.patch.object(conn, "execute", new=dies_part_way), \
                    self.assertRaises(KeyboardInterrupt):
                recall.migrate_message_columns(conn)

            leftovers = fetch_all(
                conn, "SELECT name FROM sqlite_master WHERE name = 'messages_rebuilt'")
            self.assertEqual(leftovers, [])
            self.assertEqual(first_value(conn, "SELECT text FROM messages"), "still findable")

            # And the next run completes it.
            with redirect_stderr(io.StringIO()):
                recall.migrate_message_columns(conn)
            self.assertIn("role UNINDEXED", sql_text(first_value(
                conn, "SELECT sql FROM sqlite_master WHERE name = 'messages'")))
            self.assertEqual(first_value(conn, "SELECT text FROM messages"), "still findable")
        finally:
            conn.close()


class RealisticClaudeTranscripts(IndexingCase):
    """Most of a real Claude transcript is not conversation. Fixtures made only
    of user and assistant turns never exercise the code that drops the rest."""

    def test_only_the_conversation_is_indexed(self) -> None:
        path = self.corpus.claude_session("90909090-9090-9090-9090-909090909090",
                                          [*claude_noise(), claude_entry("a real turn")])
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["a real turn"])

    def test_noise_between_turns_does_not_disturb_resuming(self) -> None:
        path = self.corpus.claude_session("91919191-9191-9191-9191-919191919191",
                                          [claude_entry("first turn")])
        self.index()
        self.corpus.write(path, [*claude_noise(), claude_entry("second turn")])
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id),
                         ["first turn", "second turn"])
        self.assert_matches_rebuild()

    def test_a_session_of_nothing_but_noise_indexes_no_messages(self) -> None:
        path = self.corpus.claude_session("92929292-9292-9292-9292-929292929292",
                                          claude_noise())
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), [])

    def test_grok_drops_its_own_non_conversational_entries(self) -> None:
        path = self.corpus.grok_session(GROK_UUID,
                                        [*grok_noise(), grok_entry("a real turn")])
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["a real turn"])

    def test_grok_skips_synthetic_harness_entries(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [
            {**grok_entry("injected context"), "synthetic_reason": "context"},
            grok_entry("a real turn"),
        ])
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["a real turn"])


class GrokSummaryFields(IndexingCase):
    """summary.json is the only source of a Grok session's title and time —
    the transcript entries carry neither."""

    def directory(self) -> Path:
        return self.corpus.grok / "%2Fwork%2Fproject" / GROK_UUID

    def test_the_title_and_time_come_from_the_summary(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")], summary={
            "info": {"cwd": "/srv/app"},
            "generated_title": "the session title",
            "created_at": "2026-05-01T12:00:00.000Z",
        })
        self.index()
        row = self.session_row(path)
        self.assertEqual(row.project, "/srv/app")
        self.assertEqual(row.slug, "the session title")
        self.assertEqual(row.timestamp,
                         recall.parse_iso_timestamp("2026-05-01T12:00:00.000Z"))

    def test_the_git_root_stands_in_for_a_missing_cwd(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")],
                                        summary={"git_root_dir": "/srv/repo"})
        self.index()
        self.assertEqual(self.session_row(path).project, "/srv/repo")

    def test_the_session_summary_stands_in_for_a_missing_title(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")],
                                        summary={"session_summary": "a summary line"})
        self.index()
        self.assertEqual(self.session_row(path).slug, "a summary line")

    def test_without_a_summary_the_project_comes_from_the_directory_name(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")],
                                        cwd="/home/u/my project")
        self.index()
        self.assertEqual(self.session_row(path).project, "/home/u/my project")

    def test_a_corrupt_summary_does_not_stop_the_session_being_indexed(self) -> None:
        path = self.corpus.grok_session(GROK_UUID, [grok_entry("a turn")])
        (self.directory() / "summary.json").write_text("{not json", encoding="utf-8")
        self.corpus.stamp(path)
        self.index()
        self.assertEqual(self.texts(self.session_row(path).session_id), ["a turn"])


class DatabaseLocationMigration(IndexingCase):
    """The index used to live in ~/.claude. Moving it is the only reason an
    upgrade from that era keeps its history."""

    def test_an_index_at_the_old_path_is_moved(self) -> None:
        with pointed_at(self.corpus, self.db):
            old = recall.CLAUDE_DIR / "recall.db"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"the old index")
            for suffix in ("-wal", "-shm"):
                Path(str(old) + suffix).write_bytes(b"sidecar" + suffix.encode())

            recall.migrate_db_location()

            self.assertFalse(old.exists())
            self.assertEqual(Path(self.db).read_bytes(), b"the old index")
            for suffix in ("-wal", "-shm"):
                self.assertEqual(Path(self.db + suffix).read_bytes(),
                                 b"sidecar" + suffix.encode())

    def test_an_index_already_at_the_new_path_is_left_alone(self) -> None:
        with pointed_at(self.corpus, self.db):
            old = recall.CLAUDE_DIR / "recall.db"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"the old index")
            Path(self.db).write_bytes(b"the current index")

            recall.migrate_db_location()

            self.assertTrue(old.exists())
            self.assertEqual(Path(self.db).read_bytes(), b"the current index")

    def test_nothing_at_the_old_path_is_a_no_op(self) -> None:
        with pointed_at(self.corpus, self.db):
            recall.migrate_db_location()
            self.assertFalse(Path(self.db).exists())


class SessionScan(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.corpus: Corpus = Corpus(self.tmp.name)
        self.db: Path = Path(self.tmp.name) / "index.db"
        self.addCleanup(self.tmp.cleanup)

    def scanned(self) -> set[str]:
        with pointed_at(self.corpus, self.db):
            return {scanned.path for scanned in recall.scan_session_files([])}

    def test_hidden_names_are_skipped_unless_spelled_out(self) -> None:
        kept = [
            self.corpus.write(self.corpus.claude / "proj" / "s.jsonl", [claude_entry("kept")]),
            self.corpus.antigravity_session("traj", [claude_entry("kept")]),
        ]
        self.corpus.write(self.corpus.claude / "proj" / ".s.jsonl", [claude_entry("hidden file")])
        self.corpus.write(self.corpus.claude / ".proj" / "s.jsonl", [claude_entry("hidden dir")])
        self.corpus.write(self.corpus.grok / ".old" / "chat_history.jsonl", [grok_entry("hidden dir")])
        self.corpus.antigravity_session(".traj", [claude_entry("hidden trajectory")])

        self.assertEqual(self.scanned(), {str(path) for path in kept})

    def test_symlinked_directories_are_followed(self) -> None:
        elsewhere = Path(self.tmp.name) / "elsewhere"
        self.corpus.write(elsewhere / "nested" / "s.jsonl", [claude_entry("linked")])
        (self.corpus.claude / "linked").symlink_to(elsewhere, target_is_directory=True)

        self.assertEqual(self.scanned(), {str(self.corpus.claude / "linked" / "nested" / "s.jsonl")})


if __name__ == "__main__":
    unittest.main()
