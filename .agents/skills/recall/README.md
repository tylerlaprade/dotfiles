# recall

Ever lost a conversation session with Claude Code, Codex, or Grok and wish you could resume it? This skill lets agents search across all your past conversations with full-text search. Builds a SQLite FTS5 index over `~/.claude/projects/`, `~/.codex/sessions/`, and `~/.grok/sessions/**/chat_history.jsonl` with BM25 ranking, Porter stemming, and incremental updates.

This is a fork of [arjunkmrm/recall](https://github.com/arjunkmrm/recall), taken at
0.2.2. It replaces upstream's `pi` support with Grok, indexes only what a session
has added since the last run, and serialises the index so several agents can
search at once. See [CHANGELOG.md](CHANGELOG.md) for the full divergence.

## Install

It lives in this dotfiles repo at `.agents/skills/recall`, symlinked into
`~/.claude/skills/` by `scripts/sync/sync-dotfiles.sh`. There is nothing to
install.

Do not run `npx skills add arjunkmrm/recall`. It defaults to project scope,
which deletes `.agents/skills/recall` and replaces it with upstream.

Then use `/recall` in Claude Code, Codex, or Grok, or ask "find a past session where we talked about foo" (you might need to restart the agent).

## How it works

```
  ~/.claude/projects/**/*.jsonl ─────────────┐
  ~/.codex/sessions/**/*.jsonl ──────────────┼─▶ Index ──▶ ~/.recall.db (SQLite FTS5, 0600)
  ~/.grok/sessions/**/chat_history.jsonl ────┘      │
                                                    │  reads only what was appended
                                                    │
  Query ──▶ FTS5 Match ──▶ BM25 rank ──▶ Recency boost ──▶ Results
                │                    [half-life: 30 days]
                │  [Porter stemming
                │   phrase/boolean/prefix]
                ▼
         snippet extraction
         highlighted excerpts
```

- Indexes user/assistant messages into a SQLite FTS5 database at `~/.recall.db`, created readable only by you
- Skips tool_use, tool_result, thinking, synthetic harness context, and image blocks
- Results ranked by BM25 with a slight recency bias (recent sessions get up to a 20% boost, decaying with a 30-day half-life)
- Results tagged `[claude]`, `[codex]`, or `[grok]` with highlighted excerpts
- Hyphenated search terms are split into quoted words, because FTS5 reads a bare `-` as NOT
- No dependencies — Python 3.9+ stdlib only. POSIX only, since it uses `fcntl` for locking

### Reading only what is new

The first run reads every session in full. After that, each session remembers
how far it was read, along with a hash of the 4 KB before that point. If the
hash still matches, only the bytes added since are parsed and inserted — no
delete, no re-parse. If it does not, the session was truncated, replaced, or had
a message removed from the middle, and it is read again from the start.

Grok is always read in full: it rewrites the whole of `chat_history.jsonl`
through a temp file every time it saves, so a byte offset into one means
nothing.

Change what a parser keeps or drops and the already-indexed part of every
session would keep its old parsing. Bump `PARSER_VERSION` in
`scripts/recall.py` when that happens, and sessions get read again.

### Several agents at once

Indexing takes an exclusive lock on `~/.recall.db.lock`, so two runs cannot both
read the same resume point and both insert the same messages. A run that waits
20 seconds without getting the lock gives up, says so, and searches the index as
it stands rather than hanging.

## Tests

```bash
python3 -m unittest discover tests -v
```

Stdlib `unittest`, no dependencies. Every fixture is synthetic and built in a
temporary directory — the suite never reads your real sessions or your real
index. The test that matters most builds an index a piece at a time and asserts
it holds exactly what a full rebuild holds.

## Contributing

Bugs and ideas for this fork: open an issue here. For the original, see
[arjunkmrm/recall](https://github.com/arjunkmrm/recall/issues).
