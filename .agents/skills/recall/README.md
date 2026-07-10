# recall

Ever lost a conversation session with Claude Code, Codex, or Grok and wish you could resume it? This skill lets agents search across all your past conversations with full-text search. Builds a SQLite FTS5 index over `~/.claude/projects/`, `~/.codex/sessions/`, and `~/.grok/sessions/**/chat_history.jsonl` with BM25 ranking, Porter stemming, and incremental updates.

## Install

```bash
npx skills add arjunkmrm/recall
```

Then use `/recall` in Claude Code, Codex, or Grok, or ask "find a past session where we talked about foo" (you might need to restart the agent).

## How it works

```
  ~/.claude/projects/**/*.jsonl ─────────────┐
  ~/.codex/sessions/**/*.jsonl ──────────────┼─▶ Index ──▶ ~/.recall.db (SQLite FTS5)
  ~/.grok/sessions/**/chat_history.jsonl ────┘      │
                                                    │  incremental (mtime-based)
                                                    │
  Query ──▶ FTS5 Match ──▶ BM25 rank ──▶ Recency boost ──▶ Results
                │                    [half-life: 30 days]
                │  [Porter stemming
                │   phrase/boolean/prefix]
                ▼
         snippet extraction
         highlighted excerpts
```

- Indexes user/assistant messages into a SQLite FTS5 database at `~/.recall.db`
- First run indexes all sessions (a few seconds); subsequent runs only process new/modified files
- Skips tool_use, tool_result, thinking, synthetic harness context, and image blocks
- Results ranked by BM25 with a slight recency bias (recent sessions get up to a 20% boost, decaying with a 30-day half-life)
- Results tagged `[claude]`, `[codex]`, or `[grok]` with highlighted excerpts
- No dependencies — Python 3.9+ stdlib only (sqlite3, json, argparse)

## Contributing

Found a bug or have an idea? [Open an issue](https://github.com/arjunkmrm/recall/issues) or submit a pull request — contributions are welcome!

