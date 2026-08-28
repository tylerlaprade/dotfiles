"""Tests for the Antigravity and OpenCode sources.

These two break assumptions the older sources share. An Antigravity trajectory
is mostly tool steps, and the typed message arrives wrapped in markup with
harness blocks appended. OpenCode keeps every session as rows in one SQLite
database rather than a file each, so it has no mtime of its own and no byte
offset to resume from.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import (
    Corpus,
    antigravity_entry,
    antigravity_noise,
    contents,
    index,
    recall,
)


class AntigravityIndexing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.corpus = Corpus(self.tmp.name)
        self.db = Path(self.tmp.name) / "index.db"
        self.addCleanup(self.tmp.cleanup)

    def indexed_messages(self, session_id):
        _, messages = contents(self.db)
        return messages.get(session_id, {})

    def test_keeps_the_request_and_drops_the_harness_blocks(self):
        self.corpus.antigravity_session("traj-1", [
            antigravity_entry("rewrite this tagline"),
            antigravity_entry("Rewritten.", role="assistant"),
        ])
        index(self.corpus, self.db)

        self.assertEqual(
            set(self.indexed_messages("traj-1")),
            {("user", "rewrite this tagline"), ("assistant", "Rewritten.")},
        )

    def test_tool_steps_and_system_bookkeeping_are_not_messages(self):
        self.corpus.antigravity_session("traj-2",
                                        [antigravity_entry("only turn")]
                                        + antigravity_noise())
        index(self.corpus, self.db)

        self.assertEqual(set(self.indexed_messages("traj-2")),
                         {("user", "only turn")})

    def test_a_session_carries_its_source_and_first_timestamp(self):
        self.corpus.antigravity_session("traj-3", [
            antigravity_entry("later", ts="2026-01-02T00:00:00Z"),
            antigravity_entry("earlier", ts="2026-01-01T00:00:00Z"),
        ])
        index(self.corpus, self.db)

        sessions, _ = contents(self.db)
        (_, source, _, _, timestamp), = [
            row for path, row in sessions.items() if row[0] == "traj-3"
        ]
        self.assertEqual(source, "antigravity")
        self.assertEqual(timestamp,
                         recall.parse_iso_timestamp("2026-01-01T00:00:00Z"))

    def test_appended_steps_are_picked_up_without_duplicating_the_old_ones(self):
        path = self.corpus.antigravity_session("traj-4",
                                               [antigravity_entry("first")])
        index(self.corpus, self.db)
        self.corpus.write(path, [antigravity_entry("second")])
        index(self.corpus, self.db)

        self.assertEqual(
            dict(self.indexed_messages("traj-4")),
            {("user", "first"): 1, ("user", "second"): 1},
        )


class OpenCodeIndexing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.corpus = Corpus(self.tmp.name)
        self.db = Path(self.tmp.name) / "index.db"
        self.addCleanup(self.tmp.cleanup)

    def indexed_messages(self, session_id):
        _, messages = contents(self.db)
        return messages.get(session_id, {})

    def test_joins_a_message_from_its_parts_and_drops_reasoning(self):
        self.corpus.opencode_session("ses_one", [
            ("user", ["fix the tagline"]),
            ("assistant", ["Done.", "Anything else?"]),
        ])
        index(self.corpus, self.db)

        self.assertEqual(
            set(self.indexed_messages("ses_one")),
            {("user", "fix the tagline"), ("assistant", "Done.\nAnything else?")},
        )

    def test_a_session_carries_its_directory_title_and_source(self):
        self.corpus.opencode_session("ses_two", [("user", ["hello"])],
                                     cwd="/work/queenspawn", title="tagline work")
        index(self.corpus, self.db)

        sessions, _ = contents(self.db)
        (_, source, project, slug, _), = [
            row for path, row in sessions.items() if row[0] == "ses_two"
        ]
        self.assertEqual((source, project, slug),
                         ("opencode", "/work/queenspawn", "tagline work"))

    def test_sessions_in_one_database_are_indexed_separately(self):
        self.corpus.opencode_session("ses_a", [("user", ["first session"])])
        self.corpus.opencode_session("ses_b", [("user", ["second session"])])
        index(self.corpus, self.db)

        self.assertEqual(set(self.indexed_messages("ses_a")),
                         {("user", "first session")})
        self.assertEqual(set(self.indexed_messages("ses_b")),
                         {("user", "second session")})

    def test_a_changed_session_is_re_read_without_duplicating_its_messages(self):
        self.corpus.opencode_session("ses_grow", [("user", ["first"])])
        index(self.corpus, self.db)
        self.corpus.opencode_session("ses_grow", [("user", ["first"]),
                                                  ("assistant", ["second"])])
        index(self.corpus, self.db)

        self.assertEqual(
            dict(self.indexed_messages("ses_grow")),
            {("user", "first"): 1, ("assistant", "second"): 1},
        )

    def test_an_untouched_session_is_not_read_again(self):
        self.corpus.opencode_session("ses_still", [("user", ["once"])])
        self.assertEqual(index(self.corpus, self.db), 1)
        self.assertEqual(index(self.corpus, self.db), 0)


if __name__ == "__main__":
    unittest.main()
