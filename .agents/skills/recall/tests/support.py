"""Shared fixtures for the recall test suite.

Every test builds its own session files in a temporary directory. Nothing here
reads ~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.local/share/opencode, or
~/.recall.db — the suite must be safe to run on a machine with real sessions
on it. A source added to the indexer must be added to `pointed_at` too, or the
suite silently starts indexing the real one.

Session files are written through Corpus, which bumps each file's mtime by a
fixed step on every write. The indexer decides what to look at by mtime, and
tests run faster than the clock ticks, so the step is what makes them repeatable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock
from urllib.parse import quote

import recall
from recall import JSONObject, Message, fetch_all, sql_int, sql_text

if TYPE_CHECKING:
    import unittest
    from collections.abc import Generator, Iterable

BASE_MTIME = 1_800_000_000  # a fixed point in time; tests only care about order


def claude_entry(text: str, role: str = "user", cwd: str = "/work/project",
                 slug: str | None = None, ts: str | None = None) -> JSONObject:
    """One line of a Claude Code transcript."""
    entry: JSONObject = {"type": role, "cwd": cwd, "message": {"content": text}}
    if slug:
        entry["slug"] = slug
    if ts:
        entry["timestamp"] = ts
    return entry


def codex_meta(session_uuid: str, cwd: str = "/work/project",
               ts: str = "2026-01-01T00:00:00.000Z") -> JSONObject:
    """The session_meta line Codex writes first, carrying the real session id."""
    return {"timestamp": ts, "type": "session_meta",
            "payload": {"id": session_uuid, "cwd": cwd}}


def codex_entry(text: str, role: str = "user", ts: str | None = None) -> JSONObject:
    """One conversational line of a Codex rollout."""
    return {"timestamp": ts or "2026-01-01T00:01:00.000Z", "type": "response_item",
            "payload": {"role": role, "content": [{"type": "input_text", "text": text}]}}


def grok_entry(text: str, role: str = "user") -> JSONObject:
    """One line of a Grok chat_history.jsonl."""
    return {"type": role, "content": text}


def antigravity_entry(text: str, role: str = "user", ts: str = "2026-01-01T00:00:00Z") -> JSONObject:
    """One step of an Antigravity transcript.

    A user step arrives wrapped in <USER_REQUEST> with harness blocks after
    it; the parser keeps only the request.
    """
    if role == "user":
        return {"source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": ts,
                "content": f"<USER_REQUEST>\n{text}\n</USER_REQUEST>\n"
                           "<ADDITIONAL_METADATA>\nThe current local time is: "
                           "2026-01-01T00:00:00-05:00.\n</ADDITIONAL_METADATA>"}
    return {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": ts,
            "content": text}


# Steps Antigravity writes that are not turns: system bookkeeping and the tool
# calls that make up most of a trajectory.
ANTIGRAVITY_NON_MESSAGE_STEPS = (
    ("SYSTEM", "CONVERSATION_HISTORY"), ("SYSTEM", "CHECKPOINT"),
    ("MODEL", "VIEW_FILE"), ("MODEL", "LIST_DIRECTORY"),
    ("MODEL", "GENERIC"), ("MODEL", "INVOKE_SUBAGENT"),
)


def antigravity_noise(ts: str = "2026-01-01T00:00:00Z") -> list[JSONObject]:
    """Real text in steps that are dropped for their source and type alone."""
    return [{"source": source, "type": kind, "created_at": ts,
             "content": f"{kind} payload text"}
            for source, kind in ANTIGRAVITY_NON_MESSAGE_STEPS]


# Entry types Claude Code writes alongside the conversation. Real transcripts
# are largely made of these, and none of them is a message.
CLAUDE_NON_MESSAGE_TYPES = (
    "last-prompt", "mode", "permission-mode", "attachment", "agent-name",
    "system", "custom-title", "file-history-snapshot", "summary",
)

# Entry types Grok writes that are not turns.
GROK_NON_MESSAGE_TYPES = ("reasoning", "tool_result", "tool_call", "system")


def claude_noise(cwd: str = "/work/project") -> list[JSONObject]:
    """The non-conversational entries a real Claude transcript is full of.

    They carry text where the parser looks for it, so only the entry type
    stops them being indexed. Fixtures without that prove nothing.
    """
    return [{"type": kind, "cwd": cwd,
             "message": {"content": f"{kind} payload text"}}
            for kind in CLAUDE_NON_MESSAGE_TYPES]


def grok_noise() -> list[JSONObject]:
    """The same, for Grok: real text, dropped only because of the type."""
    return [{"type": kind, "content": f"{kind} payload text"}
            for kind in GROK_NON_MESSAGE_TYPES]


class Corpus:
    """A synthetic home for every source, laid out under one root."""

    def __init__(self, root: Path | str) -> None:
        self.root: Path = Path(root)
        self.claude: Path = self.root / "claude" / "projects"
        self.codex: Path = self.root / "codex" / "sessions"
        self.grok: Path = self.root / "grok" / "sessions"
        self.antigravity: Path = self.root / "antigravity" / "brain"
        self.opencode_db: Path = self.root / "opencode" / "opencode.db"
        for directory in (self.claude, self.codex, self.grok, self.antigravity):
            directory.mkdir(parents=True, exist_ok=True)
        self.opencode_db.parent.mkdir(parents=True, exist_ok=True)
        self._tick: int = 0

    # — writing —————————————————————————————————————————————————————————————

    def _stamp(self, path: Path) -> None:
        """Move a file's mtime forward so the next scan notices it."""
        self._tick += 1
        os.utime(path, (BASE_MTIME + self._tick, BASE_MTIME + self._tick))

    def write(self, path: Path | str, entries: Iterable[JSONObject], mode: str = "a") -> Path:
        """Write JSONL entries to `path`, creating parents as needed."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        self._stamp(path)
        return path

    def write_raw(self, path: Path | str, text: str, mode: str = "a") -> Path:
        """Write text verbatim — for partial lines and hand-built corruption."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(text)
        self._stamp(path)
        return path

    def stamp(self, path: Path | str) -> None:
        """Bump mtime without changing content."""
        self._stamp(Path(path))

    # — one call per source ——————————————————————————————————————————————————

    def claude_session(self, session_id: str, entries: Iterable[JSONObject],
                       project: str = "proj") -> Path:
        return self.write(self.claude / project / f"{session_id}.jsonl", entries)

    def codex_session(self, session_uuid: str, entries: Iterable[JSONObject],
                      day: str = "2026/01/01") -> Path:
        name = f"rollout-2026-01-01T00-00-00-{session_uuid}.jsonl"
        return self.write(self.codex / day / name, entries)

    def grok_session(self, session_uuid: str, entries: Iterable[JSONObject],
                     cwd: str = "/work/project", summary: JSONObject | None = None) -> Path:
        directory = self.grok / quote(cwd, safe="") / session_uuid
        directory.mkdir(parents=True, exist_ok=True)
        if summary is not None:
            (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return self.write(directory / "chat_history.jsonl", entries)

    def antigravity_session(self, session_uuid: str, entries: Iterable[JSONObject]) -> Path:
        directory = self.antigravity / session_uuid / ".system_generated" / "logs"
        return self.write(directory / "transcript.jsonl", entries)

    def opencode_session(self, session_id: str, messages: list[tuple[str, list[str]]],
                         *, cwd: str = "/work/project", title: str = "a session") -> str:
        """Write one OpenCode session, its messages, and their text parts.

        `messages` is a list of (role, [text, ...]) — a message's text arrives
        as several parts, and reasoning parts sit beside them.
        """
        created = OPENCODE_CREATED_MS
        conn = sqlite3.connect(str(self.opencode_db))
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS session (
                    id TEXT PRIMARY KEY, directory TEXT, title TEXT,
                    time_created INTEGER, time_updated INTEGER);
                CREATE TABLE IF NOT EXISTS message (
                    id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
                    data TEXT);
                CREATE TABLE IF NOT EXISTS part (
                    id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                    time_created INTEGER, data TEXT);
            """)
            self._tick += 1
            conn.execute(
                "INSERT OR REPLACE INTO session VALUES (?, ?, ?, ?, ?)",
                (session_id, cwd, title, created,
                 created + self._tick * 1000),
            )
            for m, (role, texts) in enumerate(messages):
                message_id = f"{session_id}-msg{m}"
                conn.execute(
                    "INSERT OR REPLACE INTO message VALUES (?, ?, ?, ?)",
                    (message_id, session_id, created + m,
                     json.dumps({"role": role})),
                )
                for p, text in enumerate(texts):
                    conn.execute(
                        "INSERT OR REPLACE INTO part VALUES (?, ?, ?, ?, ?)",
                        (f"{message_id}-part{p}", message_id, session_id,
                         created + p, json.dumps({"type": "text", "text": text})),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO part VALUES (?, ?, ?, ?, ?)",
                    (f"{message_id}-reasoning", message_id, session_id,
                     created + 900,
                     json.dumps({"type": "reasoning", "text": "thinking out loud"})),
                )
            conn.commit()
        finally:
            conn.close()
        return f"{self.opencode_db}{recall.OPENCODE_PATH_SEP}{session_id}"


OPENCODE_CREATED_MS = 1_800_000_000_000

_pointed_at_lock = threading.Lock()


@contextmanager
def pointed_at(corpus: Corpus, db_path: Path | str) -> Generator[None]:
    """Point the recall module at a throwaway corpus and database.

    Not reentrant, and not safe to enter from more than one thread: it swaps
    module globals, so a second exit would put the real session directories
    back while the first was still using them. That mistake is silent — the
    suite simply starts indexing whatever is in the real home — so it raises
    here instead.
    """
    if not _pointed_at_lock.acquire(blocking=False):
        raise RuntimeError(
            "pointed_at is already active; it swaps module globals, so enter it "
            "once around the work rather than inside each thread or helper"
        )
    try:
        with mock.patch.multiple(
            recall,
            DB_PATH=Path(db_path),
            DB_LOCK_PATH=Path(str(db_path) + ".lock"),
            CLAUDE_DIR=corpus.root / "claude",
            CLAUDE_PROJECTS_DIR=corpus.claude,
            CODEX_SESSIONS_DIR=corpus.codex,
            GROK_SESSIONS_DIR=corpus.grok,
            ANTIGRAVITY_BRAIN_DIR=corpus.antigravity,
            OPENCODE_DB=corpus.opencode_db,
        ):
            yield
    finally:
        _pointed_at_lock.release()


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a database with the schema in place, as main() would."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    recall.create_schema(conn)
    recall.migrate_schema(conn)
    return conn


def index(corpus: Corpus, db_path: Path | str, *, force: bool = False) -> int:
    """Run one indexing pass and return how many files it touched."""
    with pointed_at(corpus, db_path):
        conn = connect(db_path)
        try:
            return recall.index_sessions(conn, force=force).indexed
        finally:
            conn.close()


SessionRow = tuple[str, str, str, str, int]


def contents(db_path: Path | str) -> tuple[dict[str, SessionRow], dict[str, Counter[Message]]]:
    """Everything the index holds, in a form two databases can be compared by.

    Messages come back as a multiset per session, because FTS5 rowid order is
    an implementation detail and says nothing about whether the index is right.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        sessions = {
            sql_text(file_path): (sql_text(session_id), sql_text(source), sql_text(project),
                                  sql_text(slug), sql_int(timestamp))
            for file_path, session_id, source, project, slug, timestamp in fetch_all(
                conn,
                "SELECT file_path, session_id, source, project, slug, timestamp FROM sessions",
            )
        }
        messages: dict[str, Counter[Message]] = {}
        for session_id, role, text in fetch_all(conn, "SELECT session_id, role, text FROM messages"):
            messages.setdefault(sql_text(session_id), Counter())[(sql_text(role), sql_text(text))] += 1
        return sessions, messages
    finally:
        conn.close()


def assert_matches_full_rebuild(test: unittest.TestCase, corpus: Corpus,
                                incremental_db: Path | str, rebuild_db: Path | str) -> None:
    """The whole point: an index built up in pieces holds what one built at
    once holds. Any difference here is silent data loss or duplication."""
    index(corpus, rebuild_db, force=True)
    inc_sessions, inc_messages = contents(incremental_db)
    full_sessions, full_messages = contents(rebuild_db)
    test.assertEqual(inc_sessions, full_sessions, "session rows differ from a full rebuild")
    test.assertEqual(inc_messages, full_messages, "messages differ from a full rebuild")
