"""Shared fixtures for the recall test suite.

Every test builds its own session files in a temporary directory. Nothing here
reads ~/.claude, ~/.codex, ~/.grok, or ~/.recall.db — the suite must be safe to
run on a machine with real sessions on it.

Session files are written through Corpus, which bumps each file's mtime by a
fixed step on every write. The indexer decides what to look at by mtime, and
tests run faster than the clock ticks, so the step is what makes them repeatable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

# Make scripts/ importable as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import recall  # noqa: E402

BASE_MTIME = 1_800_000_000  # a fixed point in time; tests only care about order


def claude_entry(text, role="user", cwd="/work/project", slug=None, ts=None):
    """One line of a Claude Code transcript."""
    entry = {"type": role, "cwd": cwd, "message": {"content": text}}
    if slug:
        entry["slug"] = slug
    if ts:
        entry["timestamp"] = ts
    return entry


def codex_meta(session_uuid, cwd="/work/project", ts="2026-01-01T00:00:00.000Z"):
    """The session_meta line Codex writes first, carrying the real session id."""
    return {"timestamp": ts, "type": "session_meta",
            "payload": {"id": session_uuid, "cwd": cwd}}


def codex_entry(text, role="user", ts=None):
    """One conversational line of a Codex rollout."""
    return {"timestamp": ts or "2026-01-01T00:01:00.000Z", "type": "response_item",
            "payload": {"role": role, "content": [{"type": "input_text", "text": text}]}}


def grok_entry(text, role="user"):
    """One line of a Grok chat_history.jsonl."""
    return {"type": role, "content": text}


class Corpus:
    """A synthetic ~/.claude, ~/.codex and ~/.grok laid out under one root."""

    def __init__(self, root):
        self.root = Path(root)
        self.claude = self.root / "claude" / "projects"
        self.codex = self.root / "codex" / "sessions"
        self.grok = self.root / "grok" / "sessions"
        for directory in (self.claude, self.codex, self.grok):
            directory.mkdir(parents=True, exist_ok=True)
        self._tick = 0

    # — writing —————————————————————————————————————————————————————————————

    def _stamp(self, path):
        """Move a file's mtime forward so the next scan notices it."""
        self._tick += 1
        os.utime(path, (BASE_MTIME + self._tick, BASE_MTIME + self._tick))

    def write(self, path, entries, mode="a"):
        """Write JSONL entries to `path`, creating parents as needed."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        self._stamp(path)
        return path

    def write_raw(self, path, text, mode="a"):
        """Write text verbatim — for partial lines and hand-built corruption."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(text)
        self._stamp(path)
        return path

    def stamp(self, path):
        """Bump mtime without changing content."""
        self._stamp(Path(path))

    # — one call per source ——————————————————————————————————————————————————

    def claude_session(self, session_id, entries, project="proj"):
        return self.write(self.claude / project / f"{session_id}.jsonl", entries)

    def codex_session(self, session_uuid, entries, day="2026/01/01"):
        name = f"rollout-2026-01-01T00-00-00-{session_uuid}.jsonl"
        return self.write(self.codex / day / name, entries)

    def grok_session(self, session_uuid, entries, cwd="/work/project", summary=None):
        directory = self.grok / quote(cwd, safe="") / session_uuid
        directory.mkdir(parents=True, exist_ok=True)
        if summary is not None:
            (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return self.write(directory / "chat_history.jsonl", entries)


_pointed_at_something = False


@contextmanager
def pointed_at(corpus, db_path):
    """Point the recall module at a throwaway corpus and database.

    Not reentrant, and not safe to enter from more than one thread: it swaps
    module globals, so a second exit would put the real session directories
    back while the first was still using them. That mistake is silent — the
    suite simply starts indexing whatever is in the real home — so it raises
    here instead.
    """
    global _pointed_at_something
    if _pointed_at_something:
        raise RuntimeError(
            "pointed_at is already active; it swaps module globals, so enter it "
            "once around the work rather than inside each thread or helper"
        )

    names = ("DB_PATH", "DB_LOCK_PATH", "CLAUDE_DIR",
             "CLAUDE_PROJECTS_DIR", "CODEX_SESSIONS_DIR", "GROK_SESSIONS_DIR")
    saved = {name: getattr(recall, name) for name in names}
    _pointed_at_something = True
    recall.DB_PATH = Path(db_path)
    recall.DB_LOCK_PATH = Path(str(db_path) + ".lock")
    recall.CLAUDE_DIR = corpus.root / "claude"
    recall.CLAUDE_PROJECTS_DIR = corpus.claude
    recall.CODEX_SESSIONS_DIR = corpus.codex
    recall.GROK_SESSIONS_DIR = corpus.grok
    try:
        yield
    finally:
        _pointed_at_something = False
        for name, value in saved.items():
            setattr(recall, name, value)


def connect(db_path):
    """Open a database with the schema in place, as main() would."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    recall.create_schema(conn)
    recall.migrate_schema(conn)
    return conn


def index(corpus, db_path, force=False):
    """Run one indexing pass and return how many files it touched."""
    with pointed_at(corpus, db_path):
        conn = connect(db_path)
        try:
            return recall.index_sessions(conn, force=force)
        finally:
            conn.close()


def contents(db_path):
    """Everything the index holds, in a form two databases can be compared by.

    Messages come back as a multiset per session, because FTS5 rowid order is
    an implementation detail and says nothing about whether the index is right.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        sessions = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT file_path, session_id, source, project, slug, timestamp FROM sessions"
            )
        }
        messages = {}
        for session_id, role, text in conn.execute(
            "SELECT session_id, role, text FROM messages"
        ):
            messages.setdefault(session_id, Counter())[(role, text)] += 1
        return sessions, messages
    finally:
        conn.close()


def assert_matches_full_rebuild(test, corpus, incremental_db, rebuild_db):
    """The whole point: an index built up in pieces holds what one built at
    once holds. Any difference here is silent data loss or duplication."""
    index(corpus, rebuild_db, force=True)
    inc_sessions, inc_messages = contents(incremental_db)
    full_sessions, full_messages = contents(rebuild_db)
    test.assertEqual(inc_sessions, full_sessions, "session rows differ from a full rebuild")
    test.assertEqual(inc_messages, full_messages, "messages differ from a full rebuild")
