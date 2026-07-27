#!/usr/bin/env python3
"""Search past Claude Code, Codex, and Grok sessions using FTS5 full-text search."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import math
import time
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from glob import glob
from pathlib import Path
from urllib.parse import unquote

CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
GROK_DIR = Path.home() / ".grok"
DB_PATH = Path.home() / ".recall.db"
DB_LOCK_PATH = Path.home() / ".recall.db.lock"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"
GROK_SESSIONS_DIR = GROK_DIR / "sessions"


# Stop waiting for the indexer after this long and search the index as it stands.
# Without a cap, one stalled holder hangs every other session with no output.
LOCK_WAIT_SECONDS = 20


@contextmanager
def index_lock():
    """Hold an exclusive lock while the index is updated.

    Indexing is one write transaction spanning every file it parses, so two
    runs at once means one of them waits out SQLite's busy timeout and dies.
    Two runs sharing a resume point would also both insert the same messages.

    Yields True when the lock was taken. After LOCK_WAIT_SECONDS it gives up
    and yields False, so one stalled run leaves every other session searching a
    slightly stale index rather than hanging.
    """
    with open(DB_LOCK_PATH, "a", encoding="utf-8") as lock_file:
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


def create_schema(conn):
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
            role,
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


def migrate_schema(conn):
    """Add whatever columns an index built by an older version is missing.

    Rows keep byte_offset 0, so each session is read in full once more and
    picks up a resume point from then on. No rebuild needed.
    """
    present = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for name, definition in ADDED_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    if "parser_version" not in present:
        # Sessions already indexed were parsed by the parsers as they stand.
        # Stamping them says so, rather than making every one be read again.
        conn.execute("UPDATE sessions SET parser_version = ?", (PARSER_VERSION,))
    conn.commit()


def migrate_db_location():
    """Move recall.db from ~/.claude/ to ~/ if it exists at the old path."""
    old_path = CLAUDE_DIR / "recall.db"
    if old_path.exists() and not DB_PATH.exists():
        old_path.rename(DB_PATH)
        # Also move the WAL/SHM files if they exist
        for suffix in ("-wal", "-shm"):
            old_extra = Path(str(old_path) + suffix)
            if old_extra.exists():
                old_extra.rename(Path(str(DB_PATH) + suffix))


# Claude and Codex only ever append to a transcript. Grok rewrites the whole of
# chat_history.jsonl through a temp file on every save, so a byte offset into it
# means nothing and those files are always read in full.
APPEND_ONLY_SOURCES = {"claude", "codex"}

# Bytes before the resume point that must still match for a tail read to be safe.
# Claude can drop a message mid-file, which shifts every later byte and lands
# inside this window; 4 KB is far more than one message line.
TAIL_WINDOW = 4096

# Stored per session. Bump it when a parser starts keeping or dropping different
# text, so already-indexed sessions get read again instead of keeping a mix of
# old and new parsing forever.
PARSER_VERSION = 1

# What the index already holds for one session file.
Indexed = namedtuple(
    "Indexed",
    "session_id mtime byte_offset tail_hash parser_version project slug timestamp",
)


def read_complete_lines(path, start=0):
    """Yield (line, offset just past it) for whole lines from `start`.

    Iterating the file reads a buffer at a time, so the largest transcript here
    being a gigabyte costs no more memory than its longest line. A transcript
    an agent is writing right now can end mid-line, and that trailing fragment
    is left for the next run rather than parsed into half a message.
    """
    with open(path, "rb") as f:
        f.seek(start)
        offset = start
        for raw in f:
            # Only the last line can lack its newline, and only while it is
            # still being written.
            if not raw.endswith(b"\n"):
                return
            offset += len(raw)
            yield raw.decode("utf-8", errors="replace"), offset


def iter_entries(path, start=0):
    """Yield (decoded entry, offset just past its line) for each JSON line.

    Blank lines and lines that do not parse are skipped, the way every one of
    these formats has always been read — a half-written or corrupt line should
    cost one message, not the session.
    """
    for line, offset in read_complete_lines(path, start):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield entry, offset


def tail_hash_at(path, offset):
    """Fingerprint the bytes just before `offset` — what we indexed up to.

    Comparing this against the stored value answers the only question a tail
    read depends on: is the file still the one we left off in the middle of?
    """
    window = min(TAIL_WINDOW, offset)
    if window <= 0:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(offset - window)
            data = f.read(window)
    except OSError:
        return None
    if len(data) != window:
        return None
    return hashlib.sha256(data).hexdigest()


def resume_offset(path, offset, tail_hash, parser_version):
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


def extract_text(content):
    """Extract plain text from message content (string or array format).

    Accepts "text" (Claude), "input_text" and "output_text" (Codex) block types.
    Skips tool calls, tool results, thinking blocks, and images.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type", "") in TEXT_BLOCK_TYPES
        ]
        return "\n".join(filter(None, parts))
    return ""


def parse_iso_timestamp(ts_str):
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


# — Claude Code session parser —————————————————————————————————————————————

def parse_claude_session(path, start=0):
    """Parse a Claude Code JSONL session file.

    Returns (metadata, messages, end_offset). With `start` past 0 only the
    bytes after it are read, so metadata reflects the tail alone and the
    caller keeps what it already stored.
    """
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
    messages = []

    end_offset = start

    try:
        for entry, end_offset in iter_entries(path, start):
            etype = entry.get("type", "")

            # Extract cwd from any entry
            if not project:
                cwd = entry.get("cwd", "")
                if cwd:
                    project = cwd

            # Extract slug from any entry
            if not slug:
                slug = entry.get("slug", "") or entry.get("leafName", "")

            # Parse timestamp
            ts_raw = entry.get("timestamp")
            ts_ms = parse_iso_timestamp(ts_raw)
            if ts_ms and (earliest_ts is None or ts_ms < earliest_ts):
                earliest_ts = ts_ms

            # Determine role: check both "type" and "role" fields
            role = entry.get("role", "")
            if role not in ("user", "assistant"):
                if etype == "user" or etype == "human":
                    role = "user"
                elif etype == "assistant":
                    role = "assistant"
                else:
                    continue

            # Extract text content — handle multiple formats:
            # 1. {message: {content: "..."}} or {message: {content: [{type:"text",...}]}}
            # 2. {content: "..."} or {content: [...]}
            content = entry.get("message", {})
            if isinstance(content, dict):
                content = content.get("content", "")
            elif isinstance(content, str):
                # message field is a plain string
                pass
            else:
                content = entry.get("content", "")

            text = extract_text(content)
            if text:
                messages.append((role, text))

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    metadata = {
        "session_id": session_id,
        "source": "claude",
        "file_path": path,
        "project": project or "",
        "slug": slug or "",
        "timestamp": earliest_ts or 0,
    }
    return metadata, messages, end_offset


# — Codex session parser ———————————————————————————————————————————————————

def parse_codex_session(path, start=0):
    """Parse a Codex JSONL session file.

    Returns (metadata, messages, end_offset). With `start` past 0 only the
    bytes after it are read, so metadata reflects the tail alone and the
    caller keeps what it already stored.

    Codex sessions live in ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.
    Supports two formats:
      - Legacy: flat entries with {role, content, record_type, id, ...}
      - Current: wrapped entries with {timestamp, type, payload: {role, content, ...}}
    """
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
    messages = []

    end_offset = start

    try:
        for entry, end_offset in iter_entries(path, start):
            # Skip state snapshots (legacy format)
            if entry.get("record_type") == "state":
                continue

            # Parse timestamp (present in both formats at top level)
            ts_raw = entry.get("timestamp")
            if ts_raw:
                ts_ms = parse_iso_timestamp(ts_raw)
                if ts_ms and (earliest_ts is None or ts_ms < earliest_ts):
                    earliest_ts = ts_ms

            etype = entry.get("type", "")

            # Current format: {type: "session_meta", payload: {id, cwd, ...}}
            if etype == "session_meta":
                payload = entry.get("payload", {})
                entry_id = payload.get("id", "")
                if entry_id and session_id.startswith("rollout-"):
                    session_id = entry_id
                if not project:
                    project = payload.get("cwd", "")
                continue

            # Current format: {type: "response_item", payload: {role, content, ...}}
            # Legacy format: {role, content, ...} (no type or type="message")
            if etype == "response_item":
                payload = entry.get("payload", {})
                role = payload.get("role", "")
                content = payload.get("content", "")
            elif etype in ("event_msg", "turn_context"):
                continue
            else:
                # Legacy format — session metadata in first entry
                if not project and "id" in entry and "instructions" in entry:
                    entry_id = entry.get("id", "")
                    if entry_id and session_id.startswith("rollout-"):
                        session_id = entry_id
                    continue

                role = entry.get("role", "")
                content = entry.get("content", "")

                # Legacy: extract cwd from <environment_context> blocks
                if not project and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text", "")
                            if "Current working directory:" in text:
                                cwd_match = re.search(
                                    r"Current working directory:\s*(.+)", text
                                )
                                if cwd_match:
                                    project = cwd_match.group(1).strip()

            # Only index user and assistant messages (skip developer/system)
            if role not in ("user", "assistant"):
                continue

            text = extract_text(content)

            # Skip system/instruction blocks injected as user messages
            if not text:
                continue
            if any(marker in text for marker in CODEX_SKIP_MARKERS):
                continue

            messages.append((role, text))

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    metadata = {
        "session_id": session_id,
        "source": "codex",
        "file_path": path,
        "project": project or "",
        "slug": slug or "",
        "timestamp": earliest_ts or 0,
    }
    return metadata, messages, end_offset


# — Grok session parser ————————————————————————————————————————————————————

def parse_grok_session(path, start=0):
    """Parse a Grok chat_history.jsonl.

    Returns (metadata, messages, end_offset). With `start` past 0 only the
    bytes after it are read; summary.json is re-read either way, since Grok
    fills in the generated title after the session has begun.

    Grok sessions live in ~/.grok/sessions/<url-encoded-cwd>/<uuid>/chat_history.jsonl.
    Optional summary.json supplies cwd, title, and created_at.
    """
    path = Path(path)
    session_dir = path.parent
    session_id = session_dir.name
    project = ""
    slug = None
    earliest_ts = None
    messages = []

    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        try:
            with open(summary_path, "r", encoding="utf-8", errors="replace") as f:
                summary = json.load(f)
            info = summary.get("info") or {}
            project = info.get("cwd") or summary.get("git_root_dir") or ""
            slug = (
                summary.get("generated_title")
                or summary.get("session_summary")
                or None
            )
            ts_ms = parse_iso_timestamp(summary.get("created_at"))
            if ts_ms:
                earliest_ts = ts_ms
        except (OSError, PermissionError, json.JSONDecodeError, TypeError):
            pass

    if not project:
        # Parent dir is percent-encoded absolute cwd, e.g. %2FUsers%2F...
        project = unquote(session_dir.parent.name)

    end_offset = start

    try:
        for entry, end_offset in iter_entries(path, start):
            # Injected harness context, not real user turns
            if entry.get("synthetic_reason"):
                continue

            etype = entry.get("type", "")
            if etype in ("user", "human"):
                role = "user"
            elif etype == "assistant":
                role = "assistant"
            else:
                continue

            text = extract_text(entry.get("content", ""))
            if not text:
                continue
            if any(marker in text for marker in GROK_SKIP_MARKERS):
                continue

            messages.append((role, text))

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    metadata = {
        "session_id": session_id,
        "source": "grok",
        "file_path": str(path),
        "project": project or "",
        "slug": slug or "",
        "timestamp": earliest_ts or 0,
    }
    return metadata, messages, end_offset


PARSERS = {
    "claude": parse_claude_session,
    "codex": parse_codex_session,
    "grok": parse_grok_session,
}


def parse_session(path, source, start=0):
    """Parse one session file with the parser for its source."""
    return PARSERS[source](path, start)


# — Indexing ———————————————————————————————————————————————————————————————

def load_indexed_state(conn):
    """What the index already knows, keyed by file path.

    Keyed by path rather than session id because a session id can change — Codex
    takes its own from the first line of the file — while the path does not.
    """
    return {
        row[0]: Indexed(*row[1:])
        for row in conn.execute(
            "SELECT file_path, session_id, mtime, byte_offset, tail_hash, "
            "parser_version, project, slug, timestamp FROM sessions"
        )
    }


def scan_session_files():
    """Every session file on disk, paired with the tool that wrote it."""
    patterns = (
        (CLAUDE_PROJECTS_DIR / "**" / "*.jsonl", "claude"),
        (CODEX_SESSIONS_DIR / "**" / "*.jsonl", "codex"),
        (GROK_SESSIONS_DIR / "**" / "chat_history.jsonl", "grok"),
    )
    return [
        (fpath, source)
        for pattern, source in patterns
        for fpath in glob(str(pattern), recursive=True)
    ]


def claim_session_id(conn, session_id, fpath, claimed_by):
    """Settle which file answers to `session_id`, recording it in `claimed_by`.

    Ids are derived from the file, and most are unique, but every workflow
    journal.jsonl derives the same one. Two files that both still exist get an
    id each. If the previous holder is gone the file was moved, so this row
    inherits the id — and the messages indexed under it, which are the same
    session's and would otherwise be left behind as a duplicate.
    """
    owner = claimed_by.get(session_id)
    if owner is not None and owner != fpath:
        if os.path.exists(owner):
            digest = hashlib.sha256(fpath.encode("utf-8")).hexdigest()[:8]
            session_id = f"{session_id}@{digest}"
        else:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    claimed_by[session_id] = fpath
    return session_id


def index_sessions(conn, force=False):
    """Scan and index new/changed session files from all sources."""
    existing = load_indexed_state(conn)

    if force:
        # Drop only what can be read again. Sessions whose files are gone —
        # nearly half of them, once a tool has aged out its own transcripts —
        # exist nowhere else, and a rebuild is no reason to lose them. These
        # deletes stay in the run's transaction so an interrupted rebuild rolls
        # back rather than leaving an empty index behind.
        for fpath, prior in existing.items():
            if os.path.exists(fpath):
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (prior.session_id,))
                conn.execute("DELETE FROM messages WHERE session_id = ?", (prior.session_id,))
        existing = {path: row for path, row in existing.items() if not os.path.exists(path)}

    claimed_by = {row.session_id: path for path, row in existing.items()}
    indexed = 0

    # Disable FTS5 automerge during bulk insert to avoid repeated segment merges
    conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 0)")

    for fpath, source in scan_session_files():
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue

        prior = existing.get(fpath)
        if prior and prior.mtime == mtime and prior.parser_version == PARSER_VERSION:
            continue

        # Read only what is new, where the source is one that only appends and
        # the bytes we left off after are still the ones we hashed.
        start = 0
        if prior and source in APPEND_ONLY_SOURCES:
            start = resume_offset(fpath, prior.byte_offset, prior.tail_hash,
                                  prior.parser_version)

        result = parse_session(fpath, source, start)
        # A file that could not be read keeps whatever is already indexed for
        # it. Dropping the rows first would prune a session on a transient
        # error, and the index is the only place some of them survive.
        if result is None:
            continue

        # Whatever is not being resumed gets replaced outright.
        if prior and not start:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (prior.session_id,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (prior.session_id,))

        metadata, messages, end_offset = result
        # Only record a resume point for a source we would resume from.
        if source not in APPEND_ONLY_SOURCES:
            end_offset = 0
        tail_hash = tail_hash_at(fpath, end_offset)

        if start:
            # Only the tail was read, so merge the way a full read would: the
            # first non-empty value wins, and the timestamp is the earliest
            # seen anywhere in the file.
            session_id = prior.session_id
            stamps = [t for t in (prior.timestamp, metadata["timestamp"]) if t]
            conn.execute(
                "UPDATE sessions SET project = ?, slug = ?, timestamp = ?, "
                "mtime = ?, byte_offset = ?, tail_hash = ?, parser_version = ? "
                "WHERE session_id = ?",
                (prior.project or metadata["project"], prior.slug or metadata["slug"],
                 min(stamps) if stamps else 0,
                 mtime, end_offset, tail_hash, PARSER_VERSION, session_id),
            )
        else:
            session_id = claim_session_id(conn, metadata["session_id"], fpath, claimed_by)
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, source, file_path, project, slug, timestamp, mtime, byte_offset, tail_hash, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, metadata["source"], metadata["file_path"],
                 metadata["project"], metadata["slug"], metadata["timestamp"],
                 mtime, end_offset, tail_hash, PARSER_VERSION),
            )

        conn.executemany(
            "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
            [(session_id, role, text) for role, text in messages],
        )

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

    return indexed


# — Search —————————————————————————————————————————————————————————————————

# FTS5 reads these as syntax rather than as words to look for.
FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query(query):
    """Quote the parts of a query FTS5 would otherwise read as syntax.

    Punctuation in a search term is an error to FTS5, not a character to match:
    a bare `-` means NOT, so `claude-code` becomes `claude NOT code` and fails
    with `no such column: code`, and `recall.py`, `CI/CD` and `don't` fail the
    same way. Quoting such a term searches for its words in order, which is
    what someone typing it meant. Operators, prefix searches and phrases the
    user quoted are left alone.
    """
    parts = []
    quoted = False
    for segment in query.split('"'):
        if quoted:
            parts.append(f'"{segment}"')
        else:
            parts.append(" ".join(quote_term(term) for term in segment.split()))
        quoted = not quoted
    return " ".join(part for part in parts if part)


def quote_term(term):
    """Quote one bare term unless FTS5 can already read it as written."""
    if term in FTS_OPERATORS or re.fullmatch(r"\w+\*?", term):
        return term
    return '"{}"'.format(term.replace('"', ""))


def search(conn, query, project=None, days=None, source=None, limit=10):
    """Search indexed sessions."""
    # FTS5 auxiliary functions (bm25, snippet) don't work with GROUP BY.
    # Use a subquery to get the best-ranking rowid per session, then fetch snippets.
    query = sanitize_fts_query(query)
    fts_params = [query]
    session_filter = ""

    if project or days or source:
        subconds = []
        if project:
            subconds.append("s2.project LIKE ? || '%'")
            fts_params.append(project)
        if days:
            cutoff = int((time.time() - days * 86400) * 1000)
            subconds.append("s2.timestamp >= ?")
            fts_params.append(cutoff)
        if source:
            subconds.append("s2.source = ?")
            fts_params.append(source)
        session_filter = (
            " AND session_id IN "
            "(SELECT s2.session_id FROM sessions s2 WHERE " + " AND ".join(subconds) + ")"
        )

    # Over-fetch candidates so recency re-ranking can surface recent results
    # that pure BM25 might have ranked just outside the cutoff.
    candidate_limit = limit * 3
    fts_params.append(candidate_limit)

    # First find best-ranking session_ids.
    # FTS5's rank column is auto-populated with bm25 when using ORDER BY rank.
    inner_sql = f"""
        SELECT session_id, MIN(rank) as best_rank
        FROM messages
        WHERE messages MATCH ?{session_filter}
        GROUP BY session_id
        ORDER BY best_rank
        LIMIT ?
    """

    try:
        # Two-pass: first get sessions+ranks, then fetch snippets individually
        ranked = conn.execute(inner_sql, fts_params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Search error: {e}", file=sys.stderr)
        return []

    results = []
    now_ms = time.time() * 1000
    for session_id, rank in ranked:
        # Get session metadata
        meta = conn.execute(
            "SELECT source, file_path, project, slug, timestamp FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not meta:
            continue

        # Get snippet from the best-matching row
        snippet_row = conn.execute(
            "SELECT snippet(messages, 2, '**', '**', '...', 20) FROM messages WHERE messages MATCH ? AND session_id = ? LIMIT 1",
            (query, session_id),
        ).fetchone()
        excerpt = snippet_row[0] if snippet_row else ""

        # Apply recency bias: blend BM25 score with a time-decay boost.
        # BM25 rank is negative (more negative = better match).
        # Recency boost: 1.0 for today, decaying with a half-life of 30 days.
        timestamp = meta[4]
        if timestamp:
            age_days = max((now_ms - timestamp) / 86_400_000, 0)
            recency_boost = math.exp(-0.693 * age_days / 30)  # half-life = 30 days
        else:
            recency_boost = 0.0
        # Blend: 80% BM25, 20% recency. Recency term scales with typical BM25 magnitude.
        blended_rank = rank * (1 - 0.2 * recency_boost)

        results.append((session_id, meta[0], meta[1], meta[2], meta[3], meta[4], excerpt, blended_rank))

    # Re-sort by blended rank and trim to requested limit.
    results.sort(key=lambda r: r[7])
    return results[:limit]


def format_timestamp(ts_ms):
    """Format millisecond timestamp to date string."""
    if not ts_ms:
        return "unknown"
    try:
        ts = float(ts_ms) / 1000  # epoch ms to seconds
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError, TypeError):
        return "unknown"


def positive_int(value):
    """A result count. Zero or less reaches SQLite as "no limit" and then gets
    sliced from the wrong end, so refuse it rather than answer wrongly."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def main():
    parser = argparse.ArgumentParser(description="Search past Claude Code, Codex, and Grok sessions")
    parser.add_argument("query", help="Search query (FTS5 syntax: quotes for phrases, AND/OR/NOT)")
    parser.add_argument("--project", help="Filter to sessions from a specific project path (prefix match)")
    parser.add_argument("--days", type=int, help="Only sessions from last N days")
    parser.add_argument("--source", choices=["claude", "codex", "grok"], help="Filter by source (claude, codex, or grok)")
    parser.add_argument("--limit", type=positive_int, default=10, help="Max results (default: 10)")
    parser.add_argument("--reindex", action="store_true", help="Force full rebuild of the index")

    args = parser.parse_args()

    # Index updates write to shared SQLite and FTS5 state. Serialize that phase,
    # then release the lock so WAL-backed searches can run concurrently.
    t0 = time.time()
    with index_lock() as have_lock:
        migrate_db_location()
        # The index holds the text of every conversation, so keep it readable
        # only by its owner. The umask covers the -wal and -shm files too.
        old_umask = os.umask(0o077)
        conn = sqlite3.connect(str(DB_PATH))
        os.umask(old_umask)
        os.chmod(str(DB_PATH), 0o600)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_schema(conn)
        migrate_schema(conn)

        indexed = index_sessions(conn, force=args.reindex) if have_lock else 0
    # Counted from before the lock, so the number covers time spent waiting too.
    index_time = time.time() - t0

    if indexed > 0:
        print(f"Indexed {indexed} sessions in {index_time:.1f}s", file=sys.stderr)

    # Search
    results = search(conn, args.query, project=args.project, days=args.days, source=args.source, limit=args.limit)

    if not results:
        print("No matching sessions found.")
        conn.close()
        return

    # Counting an FTS5 table walks the whole index, so pay for it outside the
    # lock and only once we know there is a header to print.
    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"Found {len(results)} sessions (index: {total_sessions} sessions, {total_messages} messages):\n")

    for i, (session_id, source, file_path, project, slug, timestamp, excerpt, rank) in enumerate(results, 1):
        date = format_timestamp(timestamp)
        src_tag = f"[{source}]" if source else ""
        proj_name = Path(project).name if project else "unknown"
        print(f"[{i}] {date} | {slug or session_id[:12]} | {proj_name} {src_tag}")
        if project:
            print(f"    {project}")
        print(f"    ID: {session_id}")
        if file_path:
            print(f"    File: {file_path}")
        if excerpt:
            # Clean up excerpt for display
            excerpt_clean = excerpt.replace("\n", " ").strip()
            if len(excerpt_clean) > 200:
                excerpt_clean = excerpt_clean[:200] + "..."
            print(f"    > {excerpt_clean}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
