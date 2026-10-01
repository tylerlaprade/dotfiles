#!/usr/bin/env python3
"""Search past Claude Code, Codex, Grok, Antigravity, and OpenCode sessions using FTS5 full-text search."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Union
from urllib.parse import unquote

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Mapping, Sequence

CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
GROK_DIR = Path.home() / ".grok"
DB_PATH = Path.home() / ".recall.db"
DB_LOCK_PATH = Path.home() / ".recall.db.lock"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"
GROK_SESSIONS_DIR = GROK_DIR / "sessions"
# Antigravity keeps one append-only transcript per trajectory, a subagent's
# getting a directory of its own beside its parent's.
ANTIGRAVITY_BRAIN_DIR = Path.home() / ".gemini" / "antigravity-cli" / "brain"
# Spelled out rather than globbed with **, which skips a dot directory.
ANTIGRAVITY_TRANSCRIPT = Path("*") / ".system_generated" / "logs" / "transcript.jsonl"
# OpenCode keeps every session in one SQLite database rather than a file each.
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
# Separates that database from the session inside it, in the path column.
OPENCODE_PATH_SEP = "#"


# Stop waiting for the indexer after this long and search the index as it stands.
# Without a cap, one stalled holder hangs every other session with no output.
LOCK_WAIT_SECONDS: float = 20

# Exit codes, so the caller can tell "nothing matched" from "the index could
# not be read". An agent reading a broken index as an empty one concludes
# something false about what exists, so those are different exits. A degraded
# index is a third case: some session files could not be read while indexing,
# so whatever comes back — results or their absence — is drawn from a partial
# index, and "nothing exists" is not a conclusion the caller may draw from it.
# argparse usage errors keep its 2.
EXIT_NO_RESULTS = 1
EXIT_BROKEN_INDEX = 3
EXIT_DEGRADED_INDEX = 4

JSONValue = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]
JSONObject = dict[str, "JSONValue"]
parse_json: Callable[[str], JSONValue] = json.loads

SqlValue = Union[None, int, float, str, bytes]
Row = tuple[SqlValue, ...]

Message = tuple[str, str]
Skip = tuple[str, str]


def json_text(value: JSONValue) -> str:
    """A JSON string field, with anything else read as empty."""
    return value if isinstance(value, str) else ""


def json_object(value: JSONValue) -> JSONObject:
    """A JSON object field, with anything else read as empty."""
    return value if isinstance(value, dict) else {}


def fetch_all(conn: sqlite3.Connection, sql: str,
              params: Sequence[SqlValue] | Mapping[str, SqlValue] = ()) -> list[Row]:
    """Every row a query returns."""
    rows: list[Row] = conn.execute(sql, params).fetchall()
    return rows


def fetch_one(conn: sqlite3.Connection, sql: str,
              params: Sequence[SqlValue] | Mapping[str, SqlValue] = ()) -> Row | None:
    """The first row a query returns, or None."""
    rows: list[Row] = conn.execute(sql, params).fetchmany(1)
    return rows[0] if rows else None


def sql_text(value: SqlValue) -> str:
    """A TEXT column. Anything else means the database is not one this wrote."""
    if isinstance(value, str):
        return value
    raise sqlite3.DataError(f"expected text, found {value!r}")


def sql_optional_text(value: SqlValue) -> str | None:
    """A TEXT column that may be NULL."""
    return None if value is None else sql_text(value)


def sql_int(value: SqlValue) -> int:
    """An INTEGER column, with NULL read as 0."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    raise sqlite3.DataError(f"expected an integer, found {value!r}")


def sql_float(value: SqlValue) -> float:
    """A REAL column, or an integer standing in for one."""
    if isinstance(value, (int, float)):
        return float(value)
    raise sqlite3.DataError(f"expected a number, found {value!r}")


def sql_optional_float(value: SqlValue) -> float | None:
    """A REAL column that may be NULL."""
    return None if value is None else sql_float(value)


def sql_count(conn: sqlite3.Connection, sql: str) -> int:
    """The single integer a COUNT query returns."""
    row = fetch_one(conn, sql)
    return sql_int(row[0]) if row else 0


@contextmanager
def index_lock() -> Generator[bool, None, None]:
    """Hold an exclusive lock while the index is updated.

    Indexing is one write transaction spanning every file it parses, so two
    runs at once means one of them waits out SQLite's busy timeout and dies.
    Two runs sharing a resume point would also both insert the same messages.

    Yields True when the lock was taken. After LOCK_WAIT_SECONDS it gives up
    and yields False, so one stalled run leaves every other session searching a
    slightly stale index rather than hanging.
    """
    with DB_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(
                        "Another process is indexing; searching the current index.",
                        file=sys.stderr,
                    )
                    yield False
                    return
                time.sleep(0.1)
        # Closing the file releases the lock on every path, exceptions included.
        yield True


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            file_path TEXT,
            project TEXT,
            slug TEXT,
            timestamp INTEGER,
            mtime REAL,
            byte_offset INTEGER DEFAULT 0,
            tail_hash TEXT,
            parser_version INTEGER DEFAULT 0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
            session_id UNINDEXED,
            role UNINDEXED,
            text,
            tokenize='porter unicode61'
        );
    """)


ADDED_COLUMNS = (
    ("source", "TEXT DEFAULT 'claude'"),
    ("file_path", "TEXT DEFAULT ''"),
    ("byte_offset", "INTEGER DEFAULT 0"),
    ("tail_hash", "TEXT"),
    ("parser_version", "INTEGER DEFAULT 0"),
)


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add whatever columns an index built by an older version is missing.

    Rows keep byte_offset 0, so each session is read in full once more and
    picks up a resume point from then on. No rebuild needed.
    """
    present = {sql_text(row[1]) for row in fetch_all(conn, "PRAGMA table_info(sessions)")}
    for name, definition in ADDED_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    if "parser_version" not in present:
        # Anything indexed before this column existed was parsed by version 1.
        # Saying so beats making every session be read again; stamping the
        # current version instead would certify them as parsed by a parser they
        # never saw, once it is bumped.
        conn.execute("UPDATE sessions SET parser_version = 1")
    conn.commit()


def migrate_message_columns(conn: sqlite3.Connection) -> None:
    """Rebuild the message index if it still searches the role column.

    With `role` indexed, searching for "user" or "assistant" matched the role
    of almost every message rather than its text — 87% of rows for
    "assistant" — so those words behaved as wildcards and silently narrowed
    any query containing them. FTS5 column options cannot be altered, so the
    table is rebuilt from the rows already in it. Nothing is re-read from
    disk, which matters because many indexed sessions no longer have a file.
    """
    schema = fetch_one(conn, "SELECT sql FROM sqlite_master WHERE name = 'messages'")
    if not schema or "role UNINDEXED" in sql_text(schema[0]):
        return

    print("Rebuilding the message index so roles are no longer searchable...",
          file=sys.stderr)
    # One transaction, opened explicitly. Left to itself sqlite3 commits each
    # DDL statement as it runs, and a run killed part way through would leave a
    # half-built table that every later run then died on.
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE messages_rebuilt USING fts5(
                session_id UNINDEXED,
                role UNINDEXED,
                text,
                tokenize='porter unicode61'
            )
        """)
        conn.execute("INSERT INTO messages_rebuilt(session_id, role, text) "
                     "SELECT session_id, role, text FROM messages")
        conn.execute("DROP TABLE messages")
        conn.execute("ALTER TABLE messages_rebuilt RENAME TO messages")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def migrate_db_location() -> None:
    """Move recall.db from ~/.claude/ to ~/ if it exists at the old path."""
    old_path = CLAUDE_DIR / "recall.db"
    if old_path.exists() and not DB_PATH.exists():
        old_path.rename(DB_PATH)
        # Also move the WAL/SHM files if they exist
        for suffix in ("-wal", "-shm"):
            old_extra = Path(str(old_path) + suffix)
            if old_extra.exists():
                old_extra.rename(Path(str(DB_PATH) + suffix))


# Claude, Codex, and Antigravity only ever append to a transcript. Grok
# rewrites the whole of chat_history.jsonl through a temp file on every save,
# so a byte offset into it means nothing and those files are always read in
# full; an OpenCode session is rows in a database, with no offset at all.
APPEND_ONLY_SOURCES = {"claude", "codex", "antigravity"}

# Bytes before the resume point that must still match for a tail read to be safe.
# Claude can drop a message mid-file, which shifts every later byte and lands
# inside this window; 4 KB is far more than one message line.
TAIL_WINDOW = 4096

# Stored per session. Bump it when a parser starts keeping or dropping different
# text, so already-indexed sessions get read again instead of keeping a mix of
# old and new parsing forever.
PARSER_VERSION = 1


class Indexed(NamedTuple):
    """What the index already holds for one session file."""

    session_id: str
    mtime: float | None
    byte_offset: int
    tail_hash: str | None
    parser_version: int
    project: str
    slug: str
    timestamp: int


def read_complete_lines(path: str, start: int = 0) -> Iterator[tuple[str, int]]:
    """Yield (line, offset just past it) for whole lines from `start`.

    Iterating the file reads a buffer at a time, so the largest transcript here
    being a gigabyte costs no more memory than its longest line. A transcript
    an agent is writing right now can end mid-line, and that trailing fragment
    is left for the next run rather than parsed into half a message.
    """
    with Path(path).open("rb") as f:
        f.seek(start)
        offset = start
        for raw in f:
            # Only the last line can lack its newline, and only while it is
            # still being written.
            if not raw.endswith(b"\n"):
                return
            offset += len(raw)
            yield raw.decode("utf-8", errors="replace"), offset


def iter_entries(path: str, start: int = 0) -> Iterator[tuple[JSONObject, int]]:
    """Yield (decoded entry, offset just past its line) for each JSON line.

    Blank lines and lines that do not parse are skipped, the way every one of
    these formats has always been read — a half-written or corrupt line should
    cost one message, not the session.
    """
    for line, offset in read_complete_lines(path, start):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = parse_json(stripped)
        except json.JSONDecodeError:
            continue
        # Valid JSON that is not an object — a bare list or number — would
        # reach entry.get() and end the run rather than the line.
        if isinstance(entry, dict):
            yield entry, offset


def tail_hash_at(path: str, offset: int) -> str | None:
    """Fingerprint the bytes just before `offset` — what we indexed up to.

    Comparing this against the stored value answers the only question a tail
    read depends on: is the file still the one we left off in the middle of?
    """
    window = min(TAIL_WINDOW, offset)
    if window <= 0:
        return None
    try:
        with Path(path).open("rb") as f:
            f.seek(offset - window)
            data = f.read(window)
    except OSError:
        return None
    if len(data) != window:
        return None
    return hashlib.sha256(data).hexdigest()


def resume_offset(path: str, offset: int, tail_hash: str | None, parser_version: int) -> int:
    """Offset to resume parsing from, or 0 when the file must be read in full.

    A file that was only appended to still carries the bytes we hashed last
    time. One that was truncated, replaced, or had a message removed from the
    middle does not, and gets re-read from the start.
    """
    if not offset or not tail_hash or parser_version != PARSER_VERSION:
        return 0
    return offset if tail_hash_at(path, offset) == tail_hash else 0


TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
CODEX_SKIP_MARKERS = ("<user_instructions>", "<environment_context>", "<permissions instructions>", "# AGENTS.md instructions")
GROK_SKIP_MARKERS = ("<user_info>", "<system-reminder>", "<git_status>")
CONVERSATION_ROLES = ("user", "assistant")


def extract_text(content: JSONValue) -> str:
    """Extract plain text from message content (string or array format).

    Accepts "text" (Claude), "input_text" and "output_text" (Codex) block types.
    Skips tool calls, tool results, thinking blocks, and images.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            json_text(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type", "") in TEXT_BLOCK_TYPES
        ]
        return "\n".join(filter(None, parts))
    return ""


def parse_iso_timestamp(ts_str: JSONValue) -> int | None:
    """Parse ISO 8601 timestamp string to epoch milliseconds."""
    try:
        if not ts_str or not isinstance(ts_str, str):
            if isinstance(ts_str, (int, float)):
                return int(ts_str)
            return None
        # Handle "2026-03-03T00:26:57.352Z" format
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def earliest(current: int | None, candidate: int | None) -> int | None:
    """The earlier of two timestamps, where a missing or zero one never wins."""
    if candidate and (current is None or candidate < current):
        return candidate
    return current


def turn_role(entry_type: JSONValue) -> str | None:
    """The speaker an entry's type names, or None for anything but a turn."""
    if entry_type in ("user", "human"):
        return "user"
    if entry_type == "assistant":
        return "assistant"
    return None


def turn_message(role: JSONValue, content: JSONValue, skip_markers: tuple[str, ...]) -> Message | None:
    """The (role, text) a turn contributes, or None when it says nothing indexable.

    Only user and assistant turns count, and text carrying one of the source's
    harness markers was injected rather than said.
    """
    if not isinstance(role, str) or role not in CONVERSATION_ROLES:
        return None
    text = extract_text(content)
    if not text or any(marker in text for marker in skip_markers):
        return None
    return role, text


class SessionMetadata(NamedTuple):
    session_id: str
    source: str
    file_path: str
    project: str
    slug: str
    timestamp: int


class ParsedSession(NamedTuple):
    """What one parser read: metadata, messages, and where reading stopped.

    With `start` past 0 only the bytes after it were read, so metadata reflects
    the tail alone and the caller keeps what it already stored.
    """

    metadata: SessionMetadata
    messages: list[Message]
    end_offset: int


# — Claude Code session parser —————————————————————————————————————————————

def claude_turn(entry: JSONObject) -> tuple[str, JSONValue] | None:
    """The role and content of a Claude Code entry, or None when it is not a turn.

    The role comes from "role" or, failing that, "type". The content sits in
    {message: {content}}, in a plain-string message, or in a top-level content.
    """
    role: str | None = None
    declared = entry.get("role", "")
    if isinstance(declared, str) and declared in CONVERSATION_ROLES:
        role = declared
    else:
        role = turn_role(entry.get("type", ""))
    if role is None:
        return None
    message = entry.get("message", {})
    if isinstance(message, dict):
        return role, message.get("content", "")
    if isinstance(message, str):
        return role, message
    return role, entry.get("content", "")


def parse_claude_session(path: str, start: int, skipped: list[Skip]) -> ParsedSession | None:
    """Parse a Claude Code JSONL session file.

    A file that cannot be read costs nothing: its (path, reason) goes into
    `skipped` and it returns None.
    """
    project = ""
    slug = ""
    earliest_ts: int | None = None
    messages: list[Message] = []
    end_offset = start

    try:
        for entry, offset in iter_entries(path, start):
            end_offset = offset
            project = project or json_text(entry.get("cwd", ""))
            slug = slug or json_text(entry.get("slug", "")) or json_text(entry.get("leafName", ""))
            earliest_ts = earliest(earliest_ts, parse_iso_timestamp(entry.get("timestamp")))
            turn = claude_turn(entry)
            message = turn_message(*turn, ()) if turn else None
            if message:
                messages.append(message)
    except OSError as e:
        skipped.append((path, str(e)))
        return None

    metadata = SessionMetadata(Path(path).stem, "claude", path, project, slug, earliest_ts or 0)
    return ParsedSession(metadata, messages, end_offset)


# — Codex session parser ———————————————————————————————————————————————————

CODEX_CWD_RE = re.compile(r"Current working directory:\s*(.+)")


def codex_session_id(entry_id: str, session_id: str) -> str:
    """Take the id a Codex session states over the one its rollout file name implies."""
    return entry_id if entry_id and session_id.startswith("rollout-") else session_id


def codex_turn(entry: JSONObject) -> tuple[JSONValue, JSONValue]:
    """The role and content of a Codex entry, wrapped in a payload or not."""
    if entry.get("type", "") == "response_item":
        payload = json_object(entry.get("payload", {}))
        return payload.get("role", ""), payload.get("content", "")
    return entry.get("role", ""), entry.get("content", "")


def legacy_codex_cwd(content: JSONValue) -> str:
    """The working directory a legacy Codex turn states in its content blocks.

    Legacy rollouts carry it in an <environment_context> block. When several
    blocks state one, the last wins.
    """
    project = ""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                cwd_match = CODEX_CWD_RE.search(json_text(block.get("text", "")))
                if cwd_match:
                    project = cwd_match.group(1).strip()
    return project


def is_legacy_codex_header(entry: JSONObject) -> bool:
    """The first entry of a legacy rollout, carrying its id and instructions."""
    return entry.get("type", "") != "response_item" and "id" in entry and "instructions" in entry


def parse_codex_session(path: str, start: int, skipped: list[Skip]) -> ParsedSession | None:
    """Parse a Codex JSONL session file.

    A file that cannot be read costs nothing: its (path, reason) goes into
    `skipped` and it returns None.

    Codex sessions live in ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.
    Supports two formats:
      - Legacy: flat entries with {role, content, record_type, id, ...}
      - Current: wrapped entries with {timestamp, type, payload: {role, content, ...}}
    """
    session_id = Path(path).stem
    project = ""
    earliest_ts: int | None = None
    messages: list[Message] = []
    end_offset = start

    try:
        for entry, offset in iter_entries(path, start):
            end_offset = offset
            # Skip state snapshots (legacy format)
            if entry.get("record_type") == "state":
                continue
            earliest_ts = earliest(earliest_ts, parse_iso_timestamp(entry.get("timestamp")))
            etype = entry.get("type", "")

            # Current format: {type: "session_meta", payload: {id, cwd, ...}}
            if etype == "session_meta":
                payload = json_object(entry.get("payload", {}))
                session_id = codex_session_id(json_text(payload.get("id", "")), session_id)
                project = project or json_text(payload.get("cwd", ""))
                continue
            if etype in ("event_msg", "turn_context"):
                continue
            if not project and is_legacy_codex_header(entry):
                session_id = codex_session_id(json_text(entry.get("id", "")), session_id)
                continue

            role, content = codex_turn(entry)
            if etype != "response_item" and not project:
                project = legacy_codex_cwd(content)
            message = turn_message(role, content, CODEX_SKIP_MARKERS)
            if message:
                messages.append(message)
    except OSError as e:
        skipped.append((path, str(e)))
        return None

    metadata = SessionMetadata(session_id, "codex", path, project, "", earliest_ts or 0)
    return ParsedSession(metadata, messages, end_offset)


# — Grok session parser ————————————————————————————————————————————————————

class GrokSummary(NamedTuple):
    """The cwd, title, and start time Grok writes beside a session."""

    project: str
    slug: str
    timestamp: int | None


NO_GROK_SUMMARY = GrokSummary("", "", None)


def read_grok_summary(session_dir: Path) -> GrokSummary:
    """Read summary.json, treating a missing, unreadable, or malformed one as absent."""
    summary_path = session_dir / "summary.json"
    if not summary_path.is_file():
        return NO_GROK_SUMMARY
    try:
        summary = parse_json(summary_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return NO_GROK_SUMMARY
    if not isinstance(summary, dict):
        return NO_GROK_SUMMARY
    info = json_object(summary.get("info"))
    return GrokSummary(
        json_text(info.get("cwd")) or json_text(summary.get("git_root_dir")),
        json_text(summary.get("generated_title")) or json_text(summary.get("session_summary")),
        parse_iso_timestamp(summary.get("created_at")),
    )


def grok_turn(entry: JSONObject) -> tuple[str, JSONValue] | None:
    """The role and content of a Grok entry, or None when it is not a real turn."""
    # Injected harness context, not real user turns
    if entry.get("synthetic_reason"):
        return None
    role = turn_role(entry.get("type", ""))
    if role is None:
        return None
    return role, entry.get("content", "")


def parse_grok_session(path: str, start: int, skipped: list[Skip]) -> ParsedSession | None:
    """Parse a Grok chat_history.jsonl.

    summary.json is re-read on every pass, since Grok fills in the generated
    title after the session has begun. A file that cannot be read costs
    nothing: its (path, reason) goes into `skipped` and it returns None.

    Grok sessions live in ~/.grok/sessions/<url-encoded-cwd>/<uuid>/chat_history.jsonl.
    Optional summary.json supplies cwd, title, and created_at.
    """
    session_dir = Path(path).parent
    summary = read_grok_summary(session_dir)
    # Parent dir is percent-encoded absolute cwd, e.g. %2FUsers%2F...
    project = summary.project or unquote(session_dir.parent.name)
    messages: list[Message] = []
    end_offset = start

    try:
        for entry, offset in iter_entries(path, start):
            end_offset = offset
            turn = grok_turn(entry)
            message = turn_message(*turn, GROK_SKIP_MARKERS) if turn else None
            if message:
                messages.append(message)
    except OSError as e:
        skipped.append((path, str(e)))
        return None

    metadata = SessionMetadata(session_dir.name, "grok", path, project, summary.slug,
                               summary.timestamp or 0)
    return ParsedSession(metadata, messages, end_offset)


# — Antigravity session parser —————————————————————————————————————————————

ANTIGRAVITY_USER_STEP = ("USER_EXPLICIT", "USER_INPUT")
ANTIGRAVITY_ASSISTANT_STEP = ("MODEL", "PLANNER_RESPONSE")
# Antigravity wraps the typed message in <USER_REQUEST> and appends blocks the
# harness wrote — the clock, a settings change. Only the request is the user.
ANTIGRAVITY_REQUEST_RE = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL
)


def antigravity_message(entry: JSONObject) -> Message | None:
    """Return (role, text) for a transcript step, or None to skip it.

    Every other step is a tool call, a system checkpoint, or truncated
    conversation history, which the other parsers drop too.
    """
    step = (entry.get("source", ""), entry.get("type", ""))
    content = entry.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    if step == ANTIGRAVITY_USER_STEP:
        match = ANTIGRAVITY_REQUEST_RE.search(content)
        text = match.group(1) if match else content.strip()
        return ("user", text) if text else None
    if step == ANTIGRAVITY_ASSISTANT_STEP:
        return "assistant", content.strip()
    return None


def parse_antigravity_session(path: str, start: int, skipped: list[Skip]) -> ParsedSession | None:
    """Parse an Antigravity CLI transcript.

    Transcripts live in
    ~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl
    and are only appended to, so a tail read resumes from `start`. A file that
    cannot be read costs nothing: its (path, reason) goes into `skipped` and
    it returns None.

    Antigravity records no working directory anywhere in the trajectory, so
    these sessions carry no project and `--project` cannot narrow to them.
    """
    # .../brain/<session id>/.system_generated/logs/transcript.jsonl
    session_id = Path(path).parents[2].name
    earliest_ts: int | None = None
    messages: list[Message] = []
    end_offset = start

    try:
        for entry, offset in iter_entries(path, start):
            end_offset = offset
            earliest_ts = earliest(earliest_ts, parse_iso_timestamp(entry.get("created_at")))
            message = antigravity_message(entry)
            if message:
                messages.append(message)
    except OSError as e:
        skipped.append((path, str(e)))
        return None

    metadata = SessionMetadata(session_id, "antigravity", path, "", "", earliest_ts or 0)
    return ParsedSession(metadata, messages, end_offset)


# — OpenCode session parser ————————————————————————————————————————————————

def split_opencode_path(path: str) -> tuple[str, str]:
    """Split "<database>#<session id>" into its two halves."""
    db_path, _, session_id = path.rpartition(OPENCODE_PATH_SEP)
    return db_path, session_id


def opencode_role(message_data: SqlValue) -> str:
    """The role stored in an OpenCode message row's JSON, or "" when there is none."""
    if not isinstance(message_data, str):
        return ""
    try:
        decoded = parse_json(message_data)
    except json.JSONDecodeError:
        return ""
    return json_text(json_object(decoded).get("role", ""))


def opencode_part_text(part_data: SqlValue) -> str:
    """The text of an OpenCode text part, or "" for any other part."""
    if not isinstance(part_data, str) or not part_data:
        return ""
    try:
        part = parse_json(part_data)
    except json.JSONDecodeError:
        return ""
    if isinstance(part, dict) and part.get("type") == "text":
        return json_text(part.get("text", ""))
    return ""


def opencode_messages(db_path: str, session_id: str) -> list[Message]:
    """Every user and assistant message of one OpenCode session, in order.

    Text lives in `part` rows, one message having many; the reasoning and
    tool-call parts beside them are dropped the way every parser here drops
    thinking and tool use.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = fetch_all(
            conn,
            "SELECT m.id, m.data, p.data FROM message m "
            "LEFT JOIN part p ON p.message_id = m.id "
            "WHERE m.session_id = ? "
            "ORDER BY m.time_created, m.id, p.time_created, p.id",
            (session_id,),
        )
    finally:
        conn.close()

    messages: list[Message] = []
    current_id: SqlValue = None
    role = ""
    parts: list[str] = []
    for message_id, message_data, part_data in rows:
        if message_id != current_id:
            if parts:
                messages.append((role, "\n".join(parts)))
            current_id = message_id
            parts = []
            role = opencode_role(message_data)
        if role not in CONVERSATION_ROLES:
            continue
        text = opencode_part_text(part_data)
        if text:
            parts.append(text)
    if parts:
        messages.append((role, "\n".join(parts)))
    return messages


def parse_opencode_session(path: str, skipped: list[Skip]) -> ParsedSession | None:
    """Parse one session out of the OpenCode database.

    `path` is the database and the session id joined by OPENCODE_PATH_SEP,
    because the index is keyed by path and OpenCode keeps every session in the
    one file. There is no byte offset to resume from, so a session is re-read
    whenever its own time_updated moves. A session that cannot be read costs
    nothing: its (path, reason) goes into `skipped` and it returns None.
    """
    db_path, session_id = split_opencode_path(path)

    try:
        conn = sqlite3.connect(db_path)
        try:
            row = fetch_one(
                conn,
                "SELECT directory, title, time_created FROM session WHERE id = ?",
                (session_id,),
            )
        finally:
            conn.close()
        messages = opencode_messages(db_path, session_id)
        directory, title, time_created = row or ("", "", 0)
        metadata = SessionMetadata(
            session_id, "opencode", path,
            sql_optional_text(directory) or "",
            sql_optional_text(title) or "",
            sql_int(time_created),
        )
    except sqlite3.Error as e:
        skipped.append((path, str(e)))
        return None

    return ParsedSession(metadata, messages, 0)


FILE_PARSERS: dict[str, Callable[[str, int, list[Skip]], ParsedSession | None]] = {
    "claude": parse_claude_session,
    "codex": parse_codex_session,
    "grok": parse_grok_session,
    "antigravity": parse_antigravity_session,
}
SOURCES = sorted([*FILE_PARSERS, "opencode"])


def parse_session(path: str, source: str, start: int, skipped: list[Skip]) -> ParsedSession | None:
    """Parse one session file with the parser for its source.

    `skipped` collects (path, reason) for files that could not be read, so
    the run can report them instead of each parser printing on its own.
    """
    if source == "opencode":
        return parse_opencode_session(path, skipped)
    return FILE_PARSERS[source](path, start, skipped)


# — Indexing ———————————————————————————————————————————————————————————————

def load_indexed_state(conn: sqlite3.Connection) -> dict[str, Indexed]:
    """What the index already knows, keyed by file path.

    Keyed by path rather than session id because a session id can change — Codex
    takes its own from the first line of the file — while the path does not.
    """
    rows = fetch_all(
        conn,
        "SELECT file_path, session_id, mtime, byte_offset, tail_hash, "
        "parser_version, project, slug, timestamp FROM sessions",
    )
    return {
        sql_text(file_path): Indexed(
            sql_text(session_id), sql_optional_float(mtime), sql_int(byte_offset),
            sql_optional_text(tail_hash), sql_int(parser_version),
            sql_text(project), sql_text(slug), sql_int(timestamp),
        )
        for (file_path, session_id, mtime, byte_offset, tail_hash,
             parser_version, project, slug, timestamp) in rows
    }


class ScannedFile(NamedTuple):
    """A session found on disk.

    `mtime` is None for a real file — the indexer stats those itself — and a
    stored timestamp for a source whose sessions are rows rather than files.
    """

    path: str
    source: str
    mtime: float | None


def scan_opencode_sessions(skipped: list[Skip]) -> list[ScannedFile]:
    """Every session inside the OpenCode database.

    OpenCode stores sessions in one SQLite file, so each gets a path of its
    own — database and session id — and carries its own last-changed time in
    place of the file's, letting one changed session be re-read without
    touching the rest. A database that cannot be read skips every session it
    holds, and lands in `skipped` once rather than once per session.
    """
    if not OPENCODE_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(OPENCODE_DB))
        try:
            rows = fetch_all(conn, "SELECT id, time_updated, time_created FROM session")
        finally:
            conn.close()
        return [
            ScannedFile(
                f"{OPENCODE_DB}{OPENCODE_PATH_SEP}{sql_text(session_id)}",
                "opencode",
                (sql_int(time_updated) or sql_int(time_created)) / 1000,
            )
            for session_id, time_updated, time_created in rows
        ]
    except sqlite3.Error as e:
        skipped.append((str(OPENCODE_DB), str(e)))
        return []


def scan_session_files(skipped: list[Skip]) -> list[ScannedFile]:
    """Every session on disk, paired with the tool that wrote it."""
    patterns = (
        (CLAUDE_PROJECTS_DIR / "**" / "*.jsonl", "claude"),
        (CODEX_SESSIONS_DIR / "**" / "*.jsonl", "codex"),
        (GROK_SESSIONS_DIR / "**" / "chat_history.jsonl", "grok"),
        (ANTIGRAVITY_BRAIN_DIR / ANTIGRAVITY_TRANSCRIPT, "antigravity"),
    )
    found = [
        ScannedFile(fpath, source, None)
        for pattern, source in patterns
        for fpath in glob(str(pattern), recursive=True)
    ]
    return found + scan_opencode_sessions(skipped)


def claim_session_id(conn: sqlite3.Connection, session_id: str, fpath: str,
                     prior: Indexed | None, claimed_by: dict[str, str]) -> str:
    """Settle which file answers to `session_id`, recording it in `claimed_by`.

    Ids are derived from the file, and most are unique, but every workflow
    journal.jsonl derives the same one.

    A file with a row of its own is a different session that happens to share a
    name, so it gets an id of its own. Only a file the index has never seen can
    inherit an id whose holder has gone — that is a session that moved, and its
    messages move with it. Any other reading would delete a session to give its
    id away, and for many of them the index is the only copy left.
    """
    owner = claimed_by.get(session_id)
    if owner is not None and owner != fpath:
        if prior is not None or Path(owner).exists():
            digest = hashlib.sha256(fpath.encode("utf-8")).hexdigest()[:8]
            session_id = f"{session_id}@{digest}"
        else:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    claimed_by[session_id] = fpath
    return session_id


class IndexRun(NamedTuple):
    """What one indexing pass did.

    How many files it read, and which ones it could not, each paired with why.
    The caller reports the skips and takes the exit code from them — a run that
    skipped files searched a partial index.
    """

    indexed: int
    skipped: list[Skip]


def current_mtime(scanned: ScannedFile, skipped: list[Skip]) -> float | None:
    """The time a session last changed, or None when its file cannot be stat'd."""
    if scanned.mtime is not None:
        return scanned.mtime
    try:
        return Path(scanned.path).stat().st_mtime
    except OSError as e:
        skipped.append((scanned.path, str(e)))
        return None


def record_tail_read(conn: sqlite3.Connection, prior: Indexed, metadata: SessionMetadata,
                     resume_point: tuple[float, int, str | None]) -> None:
    """Update a resumed session's row the way a full read would have set it.

    Only the tail was read, so the first non-empty value wins, and the
    timestamp is the earliest seen anywhere in the file.
    """
    mtime, end_offset, tail_hash = resume_point
    stamps = [t for t in (prior.timestamp, metadata.timestamp) if t]
    conn.execute(
        "UPDATE sessions SET project = ?, slug = ?, timestamp = ?, "
        "mtime = ?, byte_offset = ?, tail_hash = ?, parser_version = ? "
        "WHERE session_id = ?",
        (prior.project or metadata.project, prior.slug or metadata.slug,
         min(stamps) if stamps else 0,
         mtime, end_offset, tail_hash, PARSER_VERSION, prior.session_id),
    )


def record_full_read(conn: sqlite3.Connection, session_id: str, metadata: SessionMetadata,
                     resume_point: tuple[float, int, str | None]) -> None:
    """Write the row for a session that was read from the start."""
    mtime, end_offset, tail_hash = resume_point
    conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, source, file_path, project, slug, timestamp, mtime, byte_offset, tail_hash, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, metadata.source, metadata.file_path,
         metadata.project, metadata.slug, metadata.timestamp,
         mtime, end_offset, tail_hash, PARSER_VERSION),
    )


def index_file(conn: sqlite3.Connection, scanned: ScannedFile, prior: Indexed | None,
               claimed_by: dict[str, str], skipped: list[Skip]) -> bool:
    """Bring the index up to date with one session. True when it was read."""
    mtime = current_mtime(scanned, skipped)
    if mtime is None:
        return False
    if prior and prior.mtime == mtime and prior.parser_version == PARSER_VERSION:
        return False

    # Read only what is new, where the source is one that only appends and
    # the bytes we left off after are still the ones we hashed.
    start = 0
    if prior and scanned.source in APPEND_ONLY_SOURCES:
        start = resume_offset(scanned.path, prior.byte_offset, prior.tail_hash,
                              prior.parser_version)

    result = parse_session(scanned.path, scanned.source, start, skipped)
    # A file that could not be read keeps whatever is already indexed for
    # it. Dropping the rows first would prune a session on a transient
    # error, and the index is the only place some of them survive.
    if result is None:
        return False

    # Whatever is not being resumed gets replaced outright.
    if prior and not start:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (prior.session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (prior.session_id,))

    # Only record a resume point for a source we would resume from.
    end_offset = result.end_offset if scanned.source in APPEND_ONLY_SOURCES else 0
    resume_point = (mtime, end_offset, tail_hash_at(scanned.path, end_offset))

    if prior is not None and start:
        session_id = prior.session_id
        record_tail_read(conn, prior, result.metadata, resume_point)
    else:
        session_id = claim_session_id(conn, result.metadata.session_id, scanned.path,
                                      prior, claimed_by)
        if prior is None:
            # An id can already carry messages without `prior` knowing: a
            # database upgraded from before file_path was stored has no
            # path to match on. Clearing them stops a re-read stacking a
            # second copy on top of the first.
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        record_full_read(conn, session_id, result.metadata, resume_point)

    conn.executemany(
        "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
        [(session_id, role, text) for role, text in result.messages],
    )
    return True


def index_sessions(conn: sqlite3.Connection, *, force: bool = False) -> IndexRun:
    """Scan and index new/changed session files from all sources."""
    existing = load_indexed_state(conn)

    if force:
        # Forget every resume point so each file is read in full. The delete
        # then happens per file, after it has been read — deleting up front
        # loses any session that stops being readable during the rebuild, and
        # sessions whose files are already gone are simply never revisited.
        existing = {
            path: row._replace(mtime=None, byte_offset=0, tail_hash=None)
            for path, row in existing.items()
        }

    claimed_by = {row.session_id: path for path, row in existing.items()}
    indexed = 0
    skipped: list[Skip] = []

    # Disable FTS5 automerge during bulk insert to avoid repeated segment merges
    conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 0)")

    for scanned in scan_session_files(skipped):
        if index_file(conn, scanned, existing.get(scanned.path), claimed_by, skipped):
            indexed += 1

    # Only a full rebuild is worth merging every segment — 'optimize' rewrites the
    # whole index, so running it after a handful of new sessions costs seconds and
    # buys nothing. Restore automerge either way; the setting lives in the db file,
    # and committing it alongside the inserts means a run killed part way through
    # can't leave merging switched off.
    conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 4)")
    if force and indexed > 0:
        conn.execute("INSERT INTO messages(messages) VALUES('optimize')")
    conn.commit()

    return IndexRun(indexed, skipped)


# — Search —————————————————————————————————————————————————————————————————

# FTS5 reads these as syntax rather than as words to look for. NEAR is left
# out on purpose: a real one is written NEAR(a b), which splits on the space
# before it gets here, so listing it would only look like support.
FTS_OPERATORS = {"AND", "OR", "NOT"}

SECONDS_PER_DAY = 86400
MS_PER_DAY = SECONDS_PER_DAY * 1000


class Scope(NamedTuple):
    """Which sessions a search or listing covers, and how many it returns.

    `project` matches by prefix. An empty project or source, or zero days,
    leaves that filter off.
    """

    project: str | None = None
    days: int | None = None
    source: str | None = None
    limit: int = 10


class Result(NamedTuple):
    """One line of output: a session and, for a search, its best excerpt."""

    session_id: str
    source: str
    file_path: str
    project: str
    slug: str
    timestamp: int
    excerpt: str
    rank: float


def scope_params(scope: Scope) -> dict[str, SqlValue]:
    """Named parameters for the session filter, NULL where a filter is off."""
    cutoff = int((time.time() - scope.days * SECONDS_PER_DAY) * 1000) if scope.days else None
    return {
        "project": scope.project or None,
        "cutoff": cutoff,
        "source": scope.source or None,
    }


LIST_SQL = """
    SELECT session_id, source, file_path, project, slug, timestamp
    FROM sessions
    WHERE (:project IS NULL OR project LIKE :project || '%')
        AND (:cutoff IS NULL OR timestamp >= :cutoff)
        AND (:source IS NULL OR source = :source)
    ORDER BY timestamp DESC
    LIMIT :limit
"""

# FTS5 auxiliary functions (bm25, snippet) don't work with GROUP BY, so this
# finds the best-ranking session_ids first and fetches snippets afterwards.
# FTS5's rank column is auto-populated with bm25 when using ORDER BY rank.
RANKED_SQL = """
    SELECT session_id, MIN(rank) as best_rank
    FROM messages
    WHERE messages MATCH :query
    GROUP BY session_id
    ORDER BY best_rank
    LIMIT :limit
"""
FILTERED_RANKED_SQL = """
    SELECT session_id, MIN(rank) as best_rank
    FROM messages
    WHERE messages MATCH :query AND session_id IN (
        SELECT session_id FROM sessions
        WHERE (:project IS NULL OR project LIKE :project || '%')
            AND (:cutoff IS NULL OR timestamp >= :cutoff)
            AND (:source IS NULL OR source = :source)
    )
    GROUP BY session_id
    ORDER BY best_rank
    LIMIT :limit
"""


def list_sessions(conn: sqlite3.Connection, scope: Scope) -> list[Result]:
    """The most recent sessions, with no text matching at all.

    What you want when the question is "what was I working on" rather than
    "where did I say X". The sessions table already holds everything a result
    line shows, so this never touches the full-text index. Rows come back in
    the shape search() returns, so there is one rendering path.
    """
    params = {**scope_params(scope), "limit": scope.limit}
    return [
        Result(sql_text(session_id), sql_text(source), sql_text(file_path),
               sql_text(project), sql_text(slug), sql_int(timestamp), "", 0.0)
        for session_id, source, file_path, project, slug, timestamp
        in fetch_all(conn, LIST_SQL, params)
    ]


def sanitize_fts_query(query: str) -> str:
    """Quote the parts of a query FTS5 would otherwise read as syntax.

    Punctuation in a search term is an error to FTS5, not a character to match:
    a bare `-` means NOT, so `claude-code` becomes `claude NOT code` and fails
    with `no such column: code`, and `recall.py`, `CI/CD` and `don't` fail the
    same way. Quoting such a term searches for its words in order, which is
    what someone typing it meant. Operators, prefix searches and phrases the
    user quoted are left alone.
    """
    parts: list[str] = []
    quoted = False
    for segment in query.split('"'):
        if quoted:
            parts.append(f'"{segment}"')
        else:
            parts.append(" ".join(quote_term(term) for term in segment.split()))
        quoted = not quoted
    return " ".join(part for part in parts if part)


def quote_term(term: str) -> str:
    """Quote one bare term unless FTS5 can already read it as written."""
    if term in FTS_OPERATORS or re.fullmatch(r"\w+\*?", term):
        return term
    return '"{}"'.format(term.replace('"', ""))


RECENCY_HALF_LIFE_DAYS = 30
RECENCY_WEIGHT = 0.2


def blended_rank(rank: float, timestamp: int, now_ms: float) -> float:
    """Blend a BM25 rank with a time-decay boost so recent sessions rise.

    BM25 rank is negative, more negative being a better match. The boost is
    1.0 for today and halves every RECENCY_HALF_LIFE_DAYS. bm25 is negative
    and results sort ascending, so a recent session has to be made *more*
    negative to move up. Subtracting instead would push it down the page —
    which is what this did until it was measured.
    """
    if timestamp:
        age_days = max((now_ms - timestamp) / MS_PER_DAY, 0)
        recency_boost = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
    else:
        recency_boost = 0.0
    return rank * (1 + RECENCY_WEIGHT * recency_boost)


def search(conn: sqlite3.Connection, query: str, scope: Scope) -> list[Result]:
    """Search indexed sessions."""
    query = sanitize_fts_query(query)
    filters = scope_params(scope)
    filtered = any(value is not None for value in filters.values())
    # Over-fetch candidates so recency re-ranking can surface recent results
    # that pure BM25 might have ranked just outside the cutoff.
    params = {**filters, "query": query, "limit": scope.limit * 3}

    try:
        ranked = fetch_all(conn, FILTERED_RANKED_SQL if filtered else RANKED_SQL, params)
    except sqlite3.OperationalError as e:
        print(f"Search error: {e}", file=sys.stderr)
        return []

    results: list[Result] = []
    now_ms = time.time() * 1000
    for session_id_value, rank in ranked:
        session_id = sql_text(session_id_value)
        meta = fetch_one(
            conn,
            "SELECT source, file_path, project, slug, timestamp FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        if not meta:
            continue
        source, file_path, project, slug, timestamp = meta
        results.append(Result(
            session_id, sql_text(source), sql_text(file_path), sql_text(project),
            sql_text(slug), sql_int(timestamp), "",
            blended_rank(sql_float(rank), sql_int(timestamp), now_ms),
        ))

    # Re-sort by blended rank and trim to requested limit.
    results.sort(key=lambda result: result.rank)

    # Excerpts cost a query each, so fetch them only for the rows that survived
    # re-ranking rather than for every candidate. Any matching row will do —
    # picking the best-ranking one costs roughly twice as much for an excerpt
    # the reader cannot tell apart.
    return [
        result._replace(excerpt=excerpt_for(conn, query, result.session_id))
        for result in results[:scope.limit]
    ]


def excerpt_for(conn: sqlite3.Connection, query: str, session_id: str) -> str:
    """A highlighted line from this session that matched the query."""
    row = fetch_one(
        conn,
        "SELECT snippet(messages, 2, '**', '**', '...', 20) FROM messages "
        "WHERE messages MATCH ? AND session_id = ? LIMIT 1",
        (query, session_id),
    )
    return sql_text(row[0]) if row else ""


def format_timestamp(ts_ms: float | None) -> str:
    """Format millisecond timestamp to date string."""
    if not ts_ms:
        return "unknown"
    try:
        ts = float(ts_ms) / 1000  # epoch ms to seconds
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError, TypeError):
        return "unknown"


# Names this many skipped files before collapsing the rest into a count. A
# directory of unreadable files should not bury the terminal, but the first
# few names are what diagnose it.
MAX_NAMED_SKIPS = 10

MAX_EXCERPT_CHARS = 200


def report_skipped(skipped: list[Skip]) -> None:
    """Name the session files indexing could not read, capped.

    Every skip means the index may be missing that file's content. The names
    are what let someone fix it, hence naming as many as stays readable
    rather than printing only a count.
    """
    count = len(skipped)
    noun = "file" if count == 1 else "files"
    print(f"Skipped {count} session {noun} during indexing:", file=sys.stderr)
    for path, reason in skipped[:MAX_NAMED_SKIPS]:
        print(f"  {path}: {reason}", file=sys.stderr)
    if count > MAX_NAMED_SKIPS:
        print(f"  ... and {count - MAX_NAMED_SKIPS} more", file=sys.stderr)


def positive_int(value: str) -> int:
    """A result count. Zero or less reaches SQLite as "no limit" and then gets
    sliced from the wrong end, so refuse it rather than answer wrongly."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


class Arguments(argparse.Namespace):
    query: str | None
    project: str | None
    days: int | None
    source: str | None
    limit: int
    reindex: bool


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(description="Search past Claude Code, Codex, and Grok sessions")
    parser.add_argument("query", nargs="?", help="Search query (FTS5 syntax: quotes for phrases, AND/OR/NOT). Omit to list recent sessions instead of searching.")
    parser.add_argument("--project", help="Filter to sessions from a specific project path (prefix match)")
    parser.add_argument("--days", type=int, help="Only sessions from last N days")
    parser.add_argument("--source", choices=SOURCES, help=f"Filter by source ({', '.join(SOURCES)})")
    parser.add_argument("--limit", type=positive_int, default=10, help="Max results (default: 10)")
    parser.add_argument("--reindex", action="store_true", help="Force full rebuild of the index")
    return parser.parse_args(namespace=Arguments())


def open_index(*, reindex: bool) -> tuple[sqlite3.Connection, IndexRun]:
    """Open the index and bring it up to date, unless another run holds the lock.

    Index updates write to shared SQLite and FTS5 state. Serialize that phase,
    then release the lock so WAL-backed searches can run concurrently.
    """
    with index_lock() as have_lock:
        migrate_db_location()
        # The index holds the text of every conversation, so keep it readable
        # only by its owner. The umask covers the -wal and -shm files too.
        old_umask = os.umask(0o077)
        try:
            conn = sqlite3.connect(str(DB_PATH))
        finally:
            os.umask(old_umask)
        DB_PATH.chmod(0o600)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # Creating and migrating write to the database, so they need the lock
        # as much as indexing does. Without it a run that gave up waiting would
        # try DDL against a database the holder still has open, and die where
        # it was supposed to fall back to searching.
        if not have_lock:
            return conn, IndexRun(0, [])
        create_schema(conn)
        migrate_schema(conn)
        migrate_message_columns(conn)
        return conn, index_sessions(conn, force=reindex)


def print_results(conn: sqlite3.Connection, header_verb: str, results: list[Result]) -> None:
    # Counting an FTS5 table walks the whole index, so pay for it outside the
    # lock and only once we know there is a header to print.
    total_sessions = sql_count(conn, "SELECT COUNT(*) FROM sessions")
    total_messages = sql_count(conn, "SELECT COUNT(*) FROM messages")
    print(f"{header_verb} {len(results)} sessions (index: {total_sessions} sessions, {total_messages} messages):\n")

    for i, result in enumerate(results, 1):
        date = format_timestamp(result.timestamp)
        src_tag = f"[{result.source}]" if result.source else ""
        proj_name = Path(result.project).name if result.project else "unknown"
        print(f"[{i}] {date} | {result.slug or result.session_id[:12]} | {proj_name} {src_tag}")
        if result.project:
            print(f"    {result.project}")
        print(f"    ID: {result.session_id}")
        if result.file_path:
            print(f"    File: {result.file_path}")
        if result.excerpt:
            # Clean up excerpt for display
            excerpt_clean = result.excerpt.replace("\n", " ").strip()
            if len(excerpt_clean) > MAX_EXCERPT_CHARS:
                excerpt_clean = excerpt_clean[:MAX_EXCERPT_CHARS] + "..."
            print(f"    > {excerpt_clean}")
        print()


def main() -> None:
    args = parse_arguments()

    t0 = time.time()
    try:
        conn, run = open_index(reindex=args.reindex)
    except (sqlite3.Error, OSError) as e:
        print(f"Cannot use the index at {DB_PATH}: {e}", file=sys.stderr)
        sys.exit(EXIT_BROKEN_INDEX)
    # Counted from before the lock, so the number covers time spent waiting too.
    index_time = time.time() - t0

    if run.indexed > 0:
        print(f"Indexed {run.indexed} sessions in {index_time:.1f}s", file=sys.stderr)

    # Skips make the index partial: whatever follows — results or their
    # absence — was drawn from it, so the exit code says so too.
    degraded = bool(run.skipped)
    if degraded:
        report_skipped(run.skipped)

    # Search for a query, or list what is there when there is none
    scope = Scope(args.project, args.days, args.source, args.limit)
    if args.query:
        results = search(conn, args.query, scope)
        nothing_found = "No matching sessions found."
        header_verb = "Found"
    else:
        results = list_sessions(conn, scope)
        nothing_found = "No sessions in the time window."
        header_verb = "Listed"

    if not results:
        print(nothing_found)
        conn.close()
        sys.exit(EXIT_DEGRADED_INDEX if degraded else EXIT_NO_RESULTS)

    print_results(conn, header_verb, results)
    conn.close()

    if degraded:
        sys.exit(EXIT_DEGRADED_INDEX)


if __name__ == "__main__":
    main()
