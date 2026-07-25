"""Tests for schema migration.

The index on a working machine is hundreds of megabytes and holds sessions
whose files were deleted years ago. Upgrading it has to happen in place, with
every existing row intact — rebuilding is not an option, and losing rows means
losing the only surviving copy of those conversations.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import Corpus, claude_entry, index, recall

# The schema as it stood before incremental indexing.
SCHEMA_0_2_2 = """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        source TEXT,
        file_path TEXT,
        project TEXT,
        slug TEXT,
        timestamp INTEGER,
        mtime REAL
    );
    CREATE VIRTUAL TABLE messages USING fts5(
        session_id UNINDEXED, role, text, tokenize='porter unicode61'
    );
"""

# The schema before `source` and `file_path` were added, which the script has
# always migrated from and still must.
SCHEMA_0_1_0 = """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        project TEXT,
        slug TEXT,
        timestamp INTEGER,
        mtime REAL
    );
    CREATE VIRTUAL TABLE messages USING fts5(
        session_id UNINDEXED, role, text, tokenize='porter unicode61'
    );
"""


def columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}


class Migration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.db = str(self.tmp / "old.db")

    def build(self, schema, rows=()):
        conn = sqlite3.connect(self.db)
        conn.executescript(schema)
        for row in rows:
            conn.execute(
                f"INSERT INTO sessions VALUES ({','.join('?' * len(row))})", row)
        conn.commit()
        return conn

    def test_adds_the_incremental_columns_in_place(self):
        conn = self.build(SCHEMA_0_2_2, [
            ("sess-a", "claude", "/gone/a.jsonl", "/work", "slug-a", 1700, 1.0),
            ("sess-b", "codex", "/gone/b.jsonl", "/work", "slug-b", 1800, 2.0),
        ])
        conn.execute("INSERT INTO messages VALUES ('sess-a', 'user', 'kept text')")
        conn.commit()

        recall.migrate_schema(conn)

        self.assertLessEqual({"byte_offset", "tail_hash", "parser_version"}, columns(conn))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
        self.assertEqual(
            conn.execute("SELECT text FROM messages").fetchone()[0], "kept text")
        conn.close()

    def test_existing_rows_start_without_a_resume_point(self):
        """They must be read in full once more rather than resumed from an
        offset nobody recorded."""
        conn = self.build(SCHEMA_0_2_2, [
            ("sess-a", "claude", "/gone/a.jsonl", "/work", "slug-a", 1700, 1.0)])
        recall.migrate_schema(conn)
        offset, tail_hash, version = conn.execute(
            "SELECT byte_offset, tail_hash, parser_version FROM sessions").fetchone()
        self.assertEqual(recall.resume_offset("/gone/a.jsonl", offset, tail_hash, version), 0)
        conn.close()

    def test_migrating_from_the_oldest_schema_adds_every_column(self):
        conn = self.build(SCHEMA_0_1_0, [("sess-a", "/work", "slug-a", 1700, 1.0)])
        recall.migrate_schema(conn)
        self.assertLessEqual(
            {"source", "file_path", "byte_offset", "tail_hash", "parser_version"},
            columns(conn))
        conn.close()

    def test_migrating_twice_changes_nothing(self):
        conn = self.build(SCHEMA_0_2_2)
        recall.migrate_schema(conn)
        first = columns(conn)
        recall.migrate_schema(conn)
        self.assertEqual(columns(conn), first)
        conn.close()

    def test_a_half_migrated_database_finishes_upgrading(self):
        """Each column is probed on its own, so a database left part way
        through an earlier upgrade still comes out whole."""
        conn = self.build(SCHEMA_0_2_2)
        conn.execute("ALTER TABLE sessions ADD COLUMN byte_offset INTEGER DEFAULT 0")
        conn.commit()
        recall.migrate_schema(conn)
        self.assertLessEqual({"byte_offset", "tail_hash", "parser_version"}, columns(conn))
        conn.close()

    def test_an_upgraded_database_indexes_incrementally_from_then_on(self):
        conn = self.build(SCHEMA_0_2_2)
        conn.close()
        corpus = Corpus(self.tmp / "corpus")
        path = corpus.claude_session("11111111-1111-1111-1111-111111111111",
                                     [claude_entry("first")])
        index(corpus, self.db)
        corpus.write(path, [claude_entry("second")])
        index(corpus, self.db)

        conn = sqlite3.connect(self.db)
        try:
            texts = [row[0] for row in conn.execute("SELECT text FROM messages")]
            offset = conn.execute("SELECT byte_offset FROM sessions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(sorted(texts), ["first", "second"])
        self.assertEqual(offset, path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
