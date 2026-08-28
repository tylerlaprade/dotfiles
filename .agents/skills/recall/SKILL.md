---
name: recall
description: >
  Search past Claude Code, Codex, Grok, Antigravity, and OpenCode sessions. Triggers: /recall, "search old conversations",
  "find a past session", "recall a previous conversation", "search session history",
  "what did we discuss", "remember when we"
metadata:
  author: tylerlaprade
  upstream: arjunkmrm/recall
  fork: diverged from upstream 0.2.2
  version: "0.5.0"
  license: MIT
---

# /recall — Search Past Agent Sessions

Search all past Claude Code, Codex, Grok, Antigravity, and OpenCode sessions
using full-text search with BM25 ranking.

## Usage

```bash
python3 ~/.claude/skills/recall/scripts/recall.py [QUERY] [--project PATH] [--days N] [--source claude|codex|grok|antigravity|opencode] [--limit N] [--reindex]
```

## Examples

```bash
# Simple keyword search
python3 ~/.claude/skills/recall/scripts/recall.py "bufferStore"

# Phrase search (exact match)
python3 ~/.claude/skills/recall/scripts/recall.py '"ACP protocol"'

# Boolean query
python3 ~/.claude/skills/recall/scripts/recall.py "rust AND async"

# Prefix search
python3 ~/.claude/skills/recall/scripts/recall.py "buffer*"

# Filter by project and recency
python3 ~/.claude/skills/recall/scripts/recall.py "state machine" --project ~/my-project --days 7

# Search only Claude Code sessions
python3 ~/.claude/skills/recall/scripts/recall.py "buffer" --source claude

# Search only Codex sessions
python3 ~/.claude/skills/recall/scripts/recall.py "buffer" --source codex

# Search only Grok sessions
python3 ~/.claude/skills/recall/scripts/recall.py "buffer" --source grok

# Search only Antigravity (agy) or OpenCode sessions
python3 ~/.claude/skills/recall/scripts/recall.py "buffer" --source antigravity
python3 ~/.claude/skills/recall/scripts/recall.py "buffer" --source opencode

# Force reindex
python3 ~/.claude/skills/recall/scripts/recall.py --reindex "test"
```

## Query Syntax (FTS5)

- **Words**: `bufferStore` — matches stemmed variants (e.g., "discussing" matches "discuss")
- **Phrases**: `"ACP protocol"` — exact phrase match
- **Boolean**: `rust AND async`, `tauri OR electron`, `NOT deprecated`
- **Prefix**: `buffer*` — matches bufferStore, bufferMap, etc.
- **Combined**: `"state machine" AND test`
- **Hyphens**: `claude-code` is split into `"claude" "code"`, since FTS5 reads a bare `-` as NOT. Quote the phrase to search it exactly.

## After Finding a Match

To resume a session, `cd` into the project directory and use the appropriate command:

```bash
# Claude Code sessions [claude]
cd /path/to/project
claude --resume SESSION_ID

# Codex sessions [codex]
cd /path/to/project
codex resume SESSION_ID

# Grok sessions [grok]
cd /path/to/project
grok --resume SESSION_ID

# Antigravity sessions [antigravity]
cd /path/to/project
agy --conversation SESSION_ID

# OpenCode sessions [opencode] — the id after the "#" in the File: line
cd /path/to/project
opencode --session SESSION_ID
```

Each result includes a `File:` path. Use it to read the raw transcript (auto-detects format):

```bash
python3 ~/.claude/skills/recall/scripts/read_session.py <File-path-from-result>
```

If results are missing `File:` paths, run `--reindex` to backfill.

## Notes

- Index is stored at `~/.recall.db` (SQLite FTS5, auto-migrated from `~/.claude/recall.db`), created readable only by you, with `~/.recall.db.lock` serializing index updates across concurrent sessions
- Indexes `~/.claude/projects/` (Claude Code), `~/.codex/sessions/` (Codex),
  `~/.grok/sessions/**/chat_history.jsonl` (Grok),
  `~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl`
  (Antigravity), and `~/.local/share/opencode/opencode.db` (OpenCode)
- First run indexes all sessions; after that only the bytes a session has added are read, except for Grok, which rewrites its whole history file on every save, and OpenCode, whose sessions are database rows re-read whenever one changes
- Omit the query to list recent sessions instead of searching
- Run tests with `python3 -m unittest discover tests -v` from the skill root
- Only user and assistant messages are indexed (tool calls, thinking blocks, state snapshots, synthetic harness context skipped)
- Results show a `[claude]`, `[codex]`, `[grok]`, `[antigravity]`, or `[opencode]` tag to indicate the source
- An OpenCode session's `File:` is `<opencode.db>#<session id>`; pass it to `read_session.py` as-is
- Antigravity records no working directory, so its sessions show no project and `--project` cannot narrow to them
