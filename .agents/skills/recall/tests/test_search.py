"""Tests for query handling.

FTS5 reads a bare hyphen as NOT, so an ordinary search term like `claude-code`
parses as `claude NOT code` and fails against a table with no column of that
name. These cover the sanitizing that stops that, and prove it end to end.
"""
from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
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


class Ranking(unittest.TestCase):
    """Recent sessions should come first among equally good matches. The blend
    that exists to do that was pushing them down the page instead."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")

    def seed(self, ages_in_days):
        """One session per age, each matching the query exactly as well."""
        now_ms = time.time() * 1000
        for i, age in enumerate(ages_in_days):
            ts = datetime.fromtimestamp(
                (now_ms - age * 86_400_000) / 1000, tz=timezone.utc)
            self.corpus.claude_session(
                f"{i:08d}-0000-0000-0000-000000000000",
                [claude_entry("distinctivetoken and some padding words",
                              ts=ts.isoformat().replace("+00:00", "Z"))])
        index(self.corpus, self.db)

    def search(self, query, **kwargs):
        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                return recall.search(conn, query, **kwargs)
            finally:
                conn.close()

    def test_the_most_recent_of_equal_matches_comes_first(self):
        self.seed([400, 200, 1])
        results = self.search("distinctivetoken", limit=3)
        self.assertEqual(len(results), 3)
        timestamps = [row[5] for row in results]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_a_session_with_no_timestamp_is_treated_as_oldest(self):
        self.seed([1])
        self.corpus.claude_session("99999999-9999-9999-9999-999999999999",
                                   [claude_entry("distinctivetoken and some padding words")])
        index(self.corpus, self.db)
        results = self.search("distinctivetoken", limit=2)
        self.assertEqual(results[-1][5], 0)


class RolesAreNotSearchable(unittest.TestCase):
    """`role` holds the literal words "user" and "assistant". Indexing it made
    both behave as wildcards matching most of the corpus."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111", [
            claude_entry("a question about rust"),
            claude_entry("an answer about rust", role="assistant"),
        ])
        self.corpus.claude_session("22222222-2222-2222-2222-222222222222",
                                   [claude_entry("the word assistant appears here")])
        index(self.corpus, self.db)

    def search(self, query):
        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                return recall.search(conn, query, limit=10)
            finally:
                conn.close()

    def test_searching_a_role_name_finds_only_real_text(self):
        results = self.search("assistant")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "22222222-2222-2222-2222-222222222222")

    def test_a_role_word_does_not_silently_narrow_a_query(self):
        self.assertEqual(len(self.search("rust")), 1)
        self.assertEqual(len(self.search("user rust")), 0)


class MessageColumnMigration(unittest.TestCase):
    def test_an_index_with_searchable_roles_is_rebuilt_in_place(self):
        """Rebuilt from the rows it already holds — many indexed sessions have
        no file left to re-read."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = str(Path(tmp.name) / "old.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE VIRTUAL TABLE messages USING fts5(
                session_id UNINDEXED, role, text, tokenize='porter unicode61');
        """)
        conn.execute("INSERT INTO messages VALUES ('gone', 'assistant', 'irreplaceable text')")
        conn.commit()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM messages WHERE messages MATCH 'assistant'").fetchone()[0], 1)

        with redirect_stderr(io.StringIO()):
            recall.migrate_message_columns(conn)

        self.assertEqual(conn.execute("SELECT text FROM messages").fetchone()[0],
                         "irreplaceable text")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM messages WHERE messages MATCH 'assistant'").fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM messages WHERE messages MATCH 'irreplaceable'").fetchone()[0], 1)
        conn.close()

    def test_an_already_migrated_index_is_left_alone(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(str(Path(tmp.name) / "new.db"))
        recall.create_schema(conn)
        before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0]
        recall.migrate_message_columns(conn)
        self.assertEqual(conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0], before)
        conn.close()



class Filters(unittest.TestCase):
    """--project, --days and --limit had no test at all, so every one of them
    could have been returning the wrong set of sessions."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")
        now_ms = time.time() * 1000
        for i, (project, age) in enumerate((("/work/alpha", 2),
                                            ("/work/alphabet", 2),
                                            ("/work/beta", 90))):
            ts = datetime.fromtimestamp((now_ms - age * 86_400_000) / 1000,
                                        tz=timezone.utc)
            self.corpus.claude_session(
                f"{i:08d}-0000-0000-0000-000000000000",
                [claude_entry("sharedtoken here", cwd=project,
                              ts=ts.isoformat().replace("+00:00", "Z"))])
        index(self.corpus, self.db)

    def search(self, query="sharedtoken", **kwargs):
        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                return recall.search(conn, query, **kwargs)
            finally:
                conn.close()

    def projects(self, results):
        return sorted(row[3] for row in results)

    def test_no_filter_finds_them_all(self):
        self.assertEqual(len(self.search()), 3)

    def test_project_matches_by_prefix(self):
        """A prefix, not an exact match — searching a parent directory should
        find the work done under it."""
        self.assertEqual(self.projects(self.search(project="/work/alpha")),
                         ["/work/alpha", "/work/alphabet"])

    def test_project_excludes_what_it_should(self):
        self.assertEqual(self.projects(self.search(project="/work/beta")),
                         ["/work/beta"])

    def test_project_matching_an_unknown_path_finds_nothing(self):
        self.assertEqual(self.search(project="/nowhere"), [])

    def test_days_keeps_only_recent_sessions(self):
        self.assertEqual(self.projects(self.search(days=30)),
                         ["/work/alpha", "/work/alphabet"])

    def test_days_wide_enough_keeps_everything(self):
        self.assertEqual(len(self.search(days=365)), 3)

    def test_filters_combine(self):
        self.assertEqual(self.projects(self.search(project="/work/alpha", days=30)),
                         ["/work/alpha", "/work/alphabet"])
        self.assertEqual(self.search(project="/work/beta", days=30), [])

    def test_limit_is_honoured(self):
        for limit in (1, 2, 3):
            with self.subTest(limit=limit):
                self.assertEqual(len(self.search(limit=limit)), limit)

    def test_limit_must_be_positive(self):
        """SQLite reads a limit below one as "no limit", after which the
        results were sliced from the wrong end."""
        for bad in ("0", "-1"):
            with self.subTest(value=bad):
                with self.assertRaises(Exception):
                    recall.positive_int(bad)
        self.assertEqual(recall.positive_int("5"), 5)


class ResultsAreReadable(unittest.TestCase):
    """The excerpt and the date are the whole of what a result shows."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111", [
            claude_entry("padding one", ts="2026-03-04T05:06:07.000Z"),
            claude_entry("the distinctivetoken appears in this line"),
            claude_entry("padding two"),
        ])
        index(self.corpus, self.db)

    def test_the_excerpt_shows_the_matching_line_highlighted(self):
        with pointed_at(self.corpus, self.db):
            conn = connect(self.db)
            try:
                results = recall.search(conn, "distinctivetoken", limit=1)
            finally:
                conn.close()
        excerpt = results[0][6]
        self.assertIn("distinctivetoken", excerpt)
        self.assertIn("**distinctivetoken**", excerpt)

    def test_a_date_is_shown_rather_than_a_raw_number(self):
        self.assertEqual(
            recall.format_timestamp(recall.parse_iso_timestamp("2026-03-04T05:06:07.000Z")),
            "2026-03-04")

    def test_a_session_with_no_timestamp_says_so(self):
        self.assertEqual(recall.format_timestamp(0), "unknown")
        self.assertEqual(recall.format_timestamp(None), "unknown")


class CorruptLines(unittest.TestCase):
    def test_a_line_that_is_not_json_costs_one_line(self):
        """Not the run. Nothing is committed until every file has been read, so
        an error escaping here discards every session parsed before it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        corpus = Corpus(Path(tmp.name) / "corpus")
        db = str(Path(tmp.name) / "index.db")
        path = corpus.claude_session("11111111-1111-1111-1111-111111111111",
                                     [claude_entry("before the corruption")])
        corpus.write_raw(path, '{"type": "user", "message": {"content": "trunca\n')
        corpus.write(path, [claude_entry("after the corruption")])
        index(corpus, db)

        conn = sqlite3.connect(db)
        try:
            texts = sorted(row[0] for row in conn.execute("SELECT text FROM messages"))
        finally:
            conn.close()
        self.assertEqual(texts, ["after the corruption", "before the corruption"])


if __name__ == "__main__":
    unittest.main()
