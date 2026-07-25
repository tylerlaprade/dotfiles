"""Tests for query handling.

FTS5 reads a bare hyphen as NOT, so an ordinary search term like `claude-code`
parses as `claude NOT code` and fails against a table with no column of that
name. These cover the sanitizing that stops that, and prove it end to end.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import Corpus, claude_entry, connect, index, pointed_at, recall


class SanitizeQuery(unittest.TestCase):
    def test_a_hyphenated_word_becomes_separate_quoted_terms(self):
        self.assertEqual(recall.sanitize_fts_query("claude-code"), '"claude" "code"')

    def test_several_hyphens_in_one_word(self):
        self.assertEqual(recall.sanitize_fts_query("one-two-three"),
                         '"one" "two" "three"')

    def test_plain_words_are_untouched(self):
        self.assertEqual(recall.sanitize_fts_query("gpg prewarm"), "gpg prewarm")

    def test_a_quoted_phrase_is_left_alone(self):
        self.assertEqual(recall.sanitize_fts_query('"exactly this-phrase"'),
                         '"exactly this-phrase"')

    def test_hyphens_outside_a_quoted_phrase_are_still_handled(self):
        self.assertEqual(recall.sanitize_fts_query('pre-commit "left alone"'),
                         '"pre" "commit" "left alone"')

    def test_an_empty_query_stays_empty(self):
        self.assertEqual(recall.sanitize_fts_query(""), "")


class SearchEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111", [
            claude_entry("we should look at the claude-code hooks docs"),
            claude_entry("running a pre-commit check now", role="assistant"),
            claude_entry("unrelated conversation about gardening"),
        ])
        index(self.corpus, self.db)

    def search(self, query, **kwargs):
        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                return recall.search(conn, query, **kwargs)
            finally:
                conn.close()

    def test_a_hyphenated_term_finds_its_session(self):
        """Before sanitizing, this raised `no such column: code` and returned
        nothing at all."""
        self.assertEqual(len(self.search("claude-code")), 1)

    def test_another_hyphenated_term(self):
        self.assertEqual(len(self.search("pre-commit")), 1)

    def test_an_ordinary_term_still_works(self):
        self.assertEqual(len(self.search("gardening")), 1)

    def test_a_term_that_is_not_there_finds_nothing(self):
        self.assertEqual(self.search("bicycle"), [])

    def test_filtering_by_source(self):
        self.assertEqual(len(self.search("gardening", source="claude")), 1)
        self.assertEqual(self.search("gardening", source="codex"), [])


class DatabasePermissions(unittest.TestCase):
    def test_the_index_is_created_readable_only_by_its_owner(self):
        """It holds the text of every conversation, so the default umask is
        not good enough."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        corpus = Corpus(Path(tmp.name) / "corpus")
        corpus.claude_session("22222222-2222-2222-2222-222222222222",
                              [claude_entry("something private")])
        db = Path(tmp.name) / "index.db"

        saved = sys.argv
        sys.argv = ["recall.py", "private"]
        try:
            with pointed_at(corpus, str(db)), redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                recall.main()
        finally:
            sys.argv = saved

        self.assertEqual(db.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
