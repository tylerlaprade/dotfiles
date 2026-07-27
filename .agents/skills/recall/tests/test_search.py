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
    """FTS5 treats punctuation as syntax, so a term containing any has to be
    quoted or the search fails outright instead of finding nothing."""

    def test_a_hyphenated_word_is_quoted(self):
        self.assertEqual(recall.sanitize_fts_query("claude-code"), '"claude-code"')

    def test_a_dotted_word_is_quoted(self):
        self.assertEqual(recall.sanitize_fts_query("recall.py"), '"recall.py"')

    def test_a_slash_is_quoted(self):
        self.assertEqual(recall.sanitize_fts_query("CI/CD"), '"CI/CD"')

    def test_an_apostrophe_is_quoted(self):
        self.assertEqual(recall.sanitize_fts_query("don't"), '"don\'t"')

    def test_plain_words_are_untouched(self):
        self.assertEqual(recall.sanitize_fts_query("gpg prewarm"), "gpg prewarm")

    def test_operators_are_untouched(self):
        self.assertEqual(recall.sanitize_fts_query("rust AND async"), "rust AND async")
        self.assertEqual(recall.sanitize_fts_query("tauri OR electron"), "tauri OR electron")

    def test_a_prefix_search_is_untouched(self):
        self.assertEqual(recall.sanitize_fts_query("buffer*"), "buffer*")

    def test_a_quoted_phrase_is_left_alone(self):
        self.assertEqual(recall.sanitize_fts_query('"exactly this-phrase"'),
                         '"exactly this-phrase"')

    def test_punctuation_outside_a_quoted_phrase_is_still_quoted(self):
        self.assertEqual(recall.sanitize_fts_query('pre-commit "left alone"'),
                         '"pre-commit" "left alone"')

    def test_an_unbalanced_quote_is_closed_rather_than_left_dangling(self):
        self.assertEqual(recall.sanitize_fts_query('foo "bar'), 'foo "bar"')

    def test_an_empty_query_stays_empty(self):
        self.assertEqual(recall.sanitize_fts_query(""), "")
        self.assertEqual(recall.sanitize_fts_query("   "), "")


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
            claude_entry("recall.py broke on CI/CD after v1.2.3, don't ask"),
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

    def test_terms_with_other_punctuation_find_their_session(self):
        """Each of these was a `fts5: syntax error` before it was quoted."""
        for query in ("recall.py", "CI/CD", "v1.2.3", "don't"):
            with self.subTest(query=query):
                self.assertEqual(len(self.search(query)), 1)

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
