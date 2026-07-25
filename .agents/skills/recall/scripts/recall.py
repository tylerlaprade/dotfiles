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
    """Serialize index updates.

    Yields (lock_file, have_lock, already_current). Index only while holding the
    lock; `already_current` is true when another process finished indexing while
    we waited. Stamp `lock_file` by file descriptor after indexing so waiters can
    see the index is fresh.
    """
    with open(DB_LOCK_PATH, "a", encoding="utf-8") as lock_file:
        observed_mtime_ns = os.fstat(lock_file.fileno()).st_mtime_ns
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(
                        "Another process is still indexing; skipping the index update.",
                        file=sys.stderr,
                    )
                    yield lock_file, False, False
                    return
                time.sleep(0.1)
        # Closing the file releases the lock on every path, exceptions included.
        yield lock_file, True, os.fstat(lock_file.fileno()).st_mtime_ns != observed_mtime_ns


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


def migrate_schema(conn):
    """Add columns if upgrading from an older schema."""
    try:
        conn.execute("SELECT source FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'claude'")
        conn.commit()
    try:
        conn.execute("SELECT file_path FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN file_path TEXT DEFAULT ''")
        conn.commit()
    # Existing rows get byte_offset 0, so each session is read in full once more
    # and picks up an offset from then on. No rebuild needed.
    try:
        conn.execute("SELECT byte_offset FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN byte_offset INTEGER DEFAULT 0")
        conn.commit()
    try:
        conn.execute("SELECT tail_hash FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN tail_hash TEXT")
        conn.commit()
    try:
        conn.execute("SELECT parser_version FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN parser_version INTEGER DEFAULT 0")
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


CHUNK_BYTES = 1 << 20


def read_complete_lines(path, start=0):
    """Yield (line, offset just past it) for whole lines from `start`.

    Read a chunk at a time, because the largest transcript here is a gigabyte
    and reading it whole would cost several times that in memory. A transcript
    an agent is writing right now can end mid-line, and that trailing fragment
    is left for the next run rather than parsed into half a message.
    """
    with open(path, "rb") as f:
        f.seek(start)
        offset = start
        pending = b""
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk:
                return
            pending += chunk
            cut = pending.rfind(b"\n")
            if cut == -1:
                continue
            block, pending = pending[: cut + 1], pending[cut + 1:]
            for raw in block.splitlines(keepends=True):
                offset += len(raw)
                yield raw.decode("utf-8", errors="replace"), offset


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
    if not ts_str or not isinstance(ts_str, str):
        if isinstance(ts_str, (int, float)):
            return int(ts_str)
        return None
    try:
        # Handle "2026-03-03T00:26:57.352Z" format
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
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
        for line, line_end in read_complete_lines(path, start):
            end_offset = line_end
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

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
        for line, line_end in read_complete_lines(path, start):
            end_offset = line_end
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

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
        for line, line_end in read_complete_lines(path, start):
            end_offset = line_end
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

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


# — Indexing ———————————————————————————————————————————————————————————————

def index_sessions(conn, force=False):
    """Scan and index new/changed session files from all sources."""
    if force:
        conn.executescript("""
            DELETE FROM sessions;
            DELETE FROM messages;
        """)

    # Get existing state keyed by file_path (stable across session_id changes)
    existing = {}
    try:
        for row in conn.execute(
            "SELECT file_path, session_id, mtime, byte_offset, tail_hash, "
            "parser_version, project, slug, timestamp FROM sessions"
        ):
            existing[row[0]] = row[1:]
    except sqlite3.OperationalError:
        pass

    # Which file already answers to each session id. Most ids come from the file
    # name and are unique, but every workflow journal.jsonl derives the same one,
    # and a shared id would mean a shared byte offset into different files.
    claimed_by = {sid: fpath for fpath, (sid, *_) in existing.items()}

    # Collect files from all sources
    sources = []

    # Claude Code: ~/.claude/projects/**/*.jsonl
    claude_pattern = str(CLAUDE_PROJECTS_DIR / "**" / "*.jsonl")
    for fpath in glob(claude_pattern, recursive=True):
        sources.append((fpath, "claude"))

    # Codex: ~/.codex/sessions/**/*.jsonl
    codex_pattern = str(CODEX_SESSIONS_DIR / "**" / "*.jsonl")
    for fpath in glob(codex_pattern, recursive=True):
        sources.append((fpath, "codex"))

    # Grok: ~/.grok/sessions/**/chat_history.jsonl
    grok_pattern = str(GROK_SESSIONS_DIR / "**" / "chat_history.jsonl")
    for fpath in glob(grok_pattern, recursive=True):
        sources.append((fpath, "grok"))

    indexed = 0

    # Disable FTS5 automerge during bulk insert to avoid repeated segment merges
    conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 0)")

    for fpath, source in sources:
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue

        prior = existing.get(fpath)
        if prior and prior[1] == mtime:
            continue

        # Read only what is new, where the source is one that only appends and
        # the bytes we left off after are still the ones we hashed.
        start = 0
        if prior and source in APPEND_ONLY_SOURCES:
            start = resume_offset(fpath, prior[2], prior[3], prior[4])

        # Whatever is not being resumed gets replaced outright.
        if prior and not start:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (prior[0],))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (prior[0],))

        if source == "claude":
            result = parse_claude_session(fpath, start)
        elif source == "codex":
            result = parse_codex_session(fpath, start)
        else:
            result = parse_grok_session(fpath, start)

        if result is None:
            continue

        metadata, messages, end_offset = result
        # Only record a resume point for a source we would resume from.
        if source not in APPEND_ONLY_SOURCES:
            end_offset = 0
        tail_hash = tail_hash_at(fpath, end_offset)

        if start:
            # Only the tail was read, so keep the metadata already stored unless
            # this run turned up something better. The earliest timestamp can
            # only have been seen at the head of the file.
            session_id = prior[0]
            stamps = [t for t in (prior[7], metadata["timestamp"]) if t]
            conn.execute(
                "UPDATE sessions SET project = ?, slug = ?, timestamp = ?, "
                "mtime = ?, byte_offset = ?, tail_hash = ?, parser_version = ? "
                "WHERE session_id = ?",
                (prior[5] or metadata["project"], prior[6] or metadata["slug"],
                 min(stamps) if stamps else 0,
                 mtime, end_offset, tail_hash, PARSER_VERSION, session_id),
            )
        else:
            session_id = metadata["session_id"]
            owner = claimed_by.get(session_id)
            if owner is not None and owner != fpath:
                if os.path.exists(owner):
                    # Both files are still here, so they need ids of their own.
                    digest = hashlib.sha256(fpath.encode("utf-8")).hexdigest()[:8]
                    session_id = f"{session_id}@{digest}"
                else:
                    # The file moved. This row inherits the id, so clear what
                    # was indexed under it rather than adding a second copy.
                    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            claimed_by[session_id] = fpath
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

def sanitize_fts_query(query):
    """Sanitize a query for FTS5 MATCH.

    FTS5 reads a bare hyphen as the NOT operator, so 'claude-code' becomes
    'claude NOT code' and errors out because there is no column named 'code'.
    Splitting hyphenated words into separately quoted terms searches for what
    was typed. Phrases the user quoted are left alone.
    """
    parts = []
    in_quote = False
    for segment in query.split('"'):
        if in_quote:
            parts.append(f'"{segment}"')
        else:
            segment = re.sub(
                r"\b(\w+(?:-\w+)+)\b",
                lambda m: " ".join(f'"{w}"' for w in m.group().split("-")),
                segment,
            )
            parts.append(segment)
        in_quote = not in_quote
    return "".join(parts)


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


def main():
    parser = argparse.ArgumentParser(description="Search past Claude Code, Codex, and Grok sessions")
    parser.add_argument("query", help="Search query (FTS5 syntax: quotes for phrases, AND/OR/NOT)")
    parser.add_argument("--project", help="Filter to sessions from a specific project path (prefix match)")
    parser.add_argument("--days", type=int, help="Only sessions from last N days")
    parser.add_argument("--source", choices=["claude", "codex", "grok"], help="Filter by source (claude, codex, or grok)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--reindex", action="store_true", help="Force full rebuild of the index")

    args = parser.parse_args()

    # Index updates write to shared SQLite and FTS5 state. Serialize that phase,
    # then release the lock so WAL-backed searches can run concurrently.
    t0 = time.time()
    with index_lock() as (lock_file, have_lock, already_current):
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

        if not have_lock or (already_current and not args.reindex):
            indexed = 0
        else:
            indexed = index_sessions(conn, force=args.reindex)
            os.utime(lock_file.fileno(), None)
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
