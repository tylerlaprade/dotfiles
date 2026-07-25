# Changelog

Forked from [arjunkmrm/recall](https://github.com/arjunkmrm/recall) at 0.2.2 on
2026-03-03. Upstream is at 0.4.1 and has moved in a different direction: its
third source is `pi` where ours is Grok, so the two cannot simply be merged.
Versions below 0.2.3 are upstream's; from 0.2.3 on they are local.

## 0.3.0 — 2026-07-25

- Index only the bytes added since the last run, rather than deleting and
  re-parsing every session file that changed. Indexing ~150 live transcripts
  went from 42s to under a second. Sessions carry a `byte_offset`, a hash of
  the 4 KB before it, and a `parser_version`; if any of the three fails to
  line up, that file is read in full as before. Existing databases upgrade in
  place — each session is read once more and picks up an offset from then on.
- Never resume into a Grok `chat_history.jsonl`. Grok rewrites the whole file
  through a temp file on every save, so a byte offset into it means nothing.
- Give each file its own session id. Every workflow `journal.jsonl` derived the
  same id from its filename, so 142 files shared one row.
- Serialize indexing across concurrent sessions with a lock at
  `~/.recall.db.lock`. After 20 seconds a run stops waiting, says so, and
  searches the index as it stands rather than hanging.
- Run FTS5 `optimize` only on `--reindex`. It rewrites the whole index, which
  cost seconds on every run and bought nothing incrementally.
- Count the index only when there is a header to print. Counting an FTS5 table
  walks all of it, and a search with no matches never prints the count.
- Give sessions with no title of their own one at the point they are shown,
  rather than storing it. A stored fallback could not be told apart from a real
  title, so a title Claude wrote after the first index was never picked up.
- Stop a renamed session file from being indexed twice. Its messages were left
  under the old row while the new path inserted its own copy, and every further
  rename doubled it again.
- Add a test suite, run with `python3 -m unittest discover tests -v`. Fixtures
  are synthetic, so it never reads real sessions or the real index.

### Taken from upstream

- Sanitize hyphens for FTS5, which reads a bare `-` as NOT. Searching
  `claude-code` failed outright with `no such column: code`.
- Create the index 0600. It holds the text of every conversation. Unlike
  upstream this also fixes the mode on an existing database.

### Still not taken from upstream

- CJK search (0.3.0), and `pi` sessions and list mode (0.4.0, 0.4.1).

## 0.2.2

- Add slight recency bias to search ranking
- Blend BM25 relevance with time-decay boost (half-life: 30 days, 20% weight)
- Over-fetch 3x candidates before re-ranking to avoid cutting off recent results

## 0.2.1

- Batch message inserts with `executemany`
- Disable FTS5 automerge during bulk insert, optimize after
- Add MIT license

### Reindex benchmarks (1939 sessions, ~50K messages)

| Version | Time |
|---|---|
| 0.2.0 | ~10.4s |
| 0.2.1 | ~7.4s |

## 0.2.0

- Add Codex session support — indexes both `~/.claude/projects/` and `~/.codex/sessions/`
- Unified search across Claude Code and Codex sessions
- Results tagged with `[claude]` or `[codex]` to show origin
- New `--source claude|codex` flag to filter by tool
- DB moved from `~/.claude/recall.db` to `~/.recall.db` (auto-migrated on first run)
- Schema migration adds `source` and `file_path` columns to existing databases
- Results now show full `File:` path — works with subagent sessions nested in subdirectories
- New `read_session.py` script for reading transcripts (auto-detects format, JSON by default, `--pretty` for human-readable)
- Concise `extract_text` using list comprehension and `TEXT_BLOCK_TYPES` set

### Backward compatibility
- DB auto-migrated from `~/.claude/recall.db` to `~/.recall.db` on first run
- `source` column defaults to `"claude"` for existing rows
- If results are missing `File:` paths, run `--reindex` to backfill

## 0.1.0

- Initial release
- FTS5 full-text search over Claude Code sessions
- BM25 ranking with snippet extraction
- Incremental indexing via file mtime tracking
- `--project`, `--days`, `--limit`, `--reindex` filters
