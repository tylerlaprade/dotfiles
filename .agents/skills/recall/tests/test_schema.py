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
from typing import NamedTuple

import recall
from recall import SqlValue, fetch_all, fetch_one, sql_text
from support import Corpus, claude_entry, index


class Schema(NamedTuple):
    create: str
    insert: str


# The schema as it stood before incremental indexing.
SCHEMA_0_2_2 = Schema("""
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
""", "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)")

# The schema before `source` and `file_path` were added, which the script has
# always migrated from and still must.
SCHEMA_0_1_0 = Schema("""
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
""", "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)")


def columns(conn: sqlite3.Connection) -> set[str]:
    return {sql_text(row[1]) for row in fetch_all(conn, "PRAGMA table_info(sessions)")}


def count_sessions(conn: sqlite3.Connection) -> int:
    return recall.sql_count(conn, "SELECT COUNT(*) FROM sessions")


def first_text(conn: sqlite3.Connection) -> str:
    row = fetch_one(conn, "SELECT text FROM messages")
    return sql_text(row[0]) if row else ""


class Migration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.tmp: Path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.db: str = str(self.tmp / "old.db")

    def build(self, schema: Schema, rows: tuple[tuple[SqlValue, ...], ...] = ()) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.executescript(schema.create)
        conn.executemany(schema.insert, rows)
        conn.commit()
        return conn

    def test_adds_the_incremental_columns_in_place(self) -> None:
        conn = self.build(SCHEMA_0_2_2, (
            ("sess-a", "claude", "/gone/a.jsonl", "/work", "slug-a", 1700, 1.0),
            ("sess-b", "codex", "/gone/b.jsonl", "/work", "slug-b", 1800, 2.0),
        ))
        conn.execute("INSERT INTO messages VALUES ('sess-a', 'user', 'kept text')")
        conn.commit()

        recall.migrate_schema(conn)

        self.assertLessEqual({"byte_offset", "tail_hash", "parser_version"}, columns(conn))
        self.assertEqual(count_sessions(conn), 2)
        self.assertEqual(first_text(conn), "kept text")
        conn.close()

    def test_existing_rows_start_without_a_resume_point(self) -> None:
        """They must be read in full once more rather than resumed from an
        offset nobody recorded."""
        conn = self.build(SCHEMA_0_2_2, (
            ("sess-a", "claude", "/gone/a.jsonl", "/work", "slug-a", 1700, 1.0),))
        recall.migrate_schema(conn)
        stored = recall.load_indexed_state(conn)["/gone/a.jsonl"]
        self.assertEqual(recall.resume_offset("/gone/a.jsonl", stored.byte_offset,
                                              stored.tail_hash, stored.parser_version), 0)
        conn.close()

    def test_migrating_from_the_oldest_schema_adds_every_column(self) -> None:
        conn = self.build(SCHEMA_0_1_0, (("sess-a", "/work", "slug-a", 1700, 1.0),))
        recall.migrate_schema(conn)
        self.assertLessEqual(
            {"source", "file_path", "byte_offset", "tail_hash", "parser_version"},
            columns(conn))
        conn.close()

    def test_migrating_twice_changes_nothing(self) -> None:
        conn = self.build(SCHEMA_0_2_2)
        recall.migrate_schema(conn)
        first = columns(conn)
        recall.migrate_schema(conn)
        self.assertEqual(columns(conn), first)
        conn.close()

    def test_a_half_migrated_database_finishes_upgrading(self) -> None:
        """Each column is probed on its own, so a database left part way
        through an earlier upgrade still comes out whole."""
        conn = self.build(SCHEMA_0_2_2)
        conn.execute("ALTER TABLE sessions ADD COLUMN byte_offset INTEGER DEFAULT 0")
        conn.commit()
        recall.migrate_schema(conn)
        self.assertLessEqual({"byte_offset", "tail_hash", "parser_version"}, columns(conn))
        conn.close()

    def test_an_upgraded_database_indexes_incrementally_from_then_on(self) -> None:
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
            texts = [sql_text(row[0]) for row in fetch_all(conn, "SELECT text FROM messages")]
            offset = recall.load_indexed_state(conn)[str(path)].byte_offset
        finally:
            conn.close()
        self.assertEqual(sorted(texts), ["first", "second"])
        self.assertEqual(offset, path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
