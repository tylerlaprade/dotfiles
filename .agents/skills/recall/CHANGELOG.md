# Changelog

Forked from [arjunkmrm/recall](https://github.com/arjunkmrm/recall) at 0.2.2 on
2026-03-03. Upstream is at 0.4.1 and has moved in a different direction: its
third source is `pi` where ours is Grok, so the two cannot simply be merged.
Versions below 0.2.3 are upstream's; from 0.2.3 on they are local.

## 0.4.0 — 2026-07-27

- Take list mode from upstream 0.4.1. Run `/recall` with no query and it lists
  recent sessions by date rather than demanding a keyword — the answer to
  "what was I working on" rather than "where did I say X". `--project`,
  `--days`, `--source` and `--limit` all still apply, and it never touches the
  full-text index.
- Cover the parts of the tool the suite had never reached: `migrate_db_location`,
  the Grok `summary.json` fields, and Claude and Grok transcripts made mostly of
  the non-conversational entries real ones are full of. The fixtures now carry
  text where the parsers look for it — without that the entry-type filter was
  never actually exercised, and removing it failed nothing.
- Stamp `parser_version` with the literal 1 on upgrade rather than whatever
  the current version happens to be, which would have certified legacy rows as
  parsed by a parser they never saw.
- Drop `NEAR` from the operator list. A real one is written `NEAR(a b)`, which
  splits on the space before it reaches the query sanitizer, so listing it only
  looked like support.

## 0.3.3 — 2026-07-27

Found by a fresh adversarial pass over the rewritten code. The first of these
was a day old.

- Rebuilding the message index is one transaction now. `executescript` commits
  each statement as it runs, so a run killed during the 12-second rebuild left
  a half-built table behind, and every run after that died on "table
  messages_rebuilt already exists" with no way out but hand-editing SQLite.
- Creating and migrating the schema now happen only with the lock held. They
  are writes, and a run that gave up waiting was doing them anyway — crashing
  where the timeout exists to fall back to searching.
- `--reindex` no longer deletes a session before re-reading it. It deleted
  every row whose file existed, then spent 35 seconds reading them; anything
  that stopped being readable in between was simply gone. Each file is now
  replaced only once it has been read, which is what the incremental path
  already did.
- Claiming a session id no longer deletes the session that held it. Two
  workflow journals derive the same id, and when the holder's file aged out
  the other inherited the id by deleting its messages — destroying a session
  that existed nowhere else. Only a file the index has never seen can inherit
  an id, which is the case that actually means the session moved.
- An upgrade from before `file_path` was stored duplicated every message,
  because the migration gives every existing row an empty path and they all
  collapse onto one key. 42 sessions in this index held every message three
  times as a result; they have been deduplicated.
- A line of valid JSON that is not an object — `[1,2,3]`, `42`, `null` — ended
  the whole run rather than being skipped like any other unusable line. Same
  for a Grok `summary.json` that is not an object.

## 0.3.2 — 2026-07-27

Two of these had been wrong since before the fork, and both were found by
measuring search results rather than reading the code.

- The recency bias ran backwards. bm25 scores are negative and results sort
  ascending, so multiplying by `(1 - 0.2 * boost)` moved recent sessions
  *later*. The feature added to surface recent work was burying it: across 15
  real queries it made the top ten older in ten of them and newer in none, and
  in two it pushed the single best match off the first page entirely.
- Roles were searchable. `role` held the literal words "user" and "assistant"
  and was an indexed column, so searching either matched 87% of every message
  in the index — 172,673 rows of 197,795, of which only 5,038 actually said
  the word. Any query containing "user" was silently narrowed to user turns.
  The index is rebuilt in place from the rows it already holds, since many
  indexed sessions no longer have a file to re-read.
- Fetch excerpts only for results that are shown. Search over-fetches three
  times the requested rows so re-ranking has room, then discarded two thirds
  of the excerpts it had just built, one FTS5 query each. `the` went from
  5.41s to 1.36s and `code` from 1.01s to 0.48s.
- `read_session.py` hid messages the index holds. It applied every skip marker
  to every format while the indexer applies each source's markers only to that
  source, so a Claude turn carrying a `<system-reminder>` was searchable but
  refused to print. It now shares the marker sets and `extract_text` with
  `recall.py` rather than keeping a copy that had drifted.
- The test suite indexed the real home. The concurrency test entered the
  fixture's global-swapping context manager inside each thread, so one thread
  restored the real session directories while the other was still scanning —
  3877 real files read on every run, and 33s instead of 2.2s.

## 0.3.1 — 2026-07-27

Everything here was found by testing the 0.3.0 work rather than reading it.

- Stop `--reindex` from destroying sessions whose files are gone. It emptied
  both tables and rebuilt from disk, so on this machine a rebuild silently
  dropped 2960 of 6908 sessions — nearly half, and the index is the only place
  they still exist. It now re-reads what it can and leaves the rest alone.
- Keep the `--reindex` deletes inside the run's transaction. They were
  committed before the rebuild started, so every concurrent search saw an empty
  index for the length of the rebuild, and a run killed part way through left
  it empty for good.
- Stop a read error from pruning a session. The old rows were deleted before
  the file was parsed, so a permission error, or a transcript rotated away
  between the scan and the read, silently dropped what was already indexed.
- Make `PARSER_VERSION` do its job. The mtime check returned before anything
  looked at it, so a bump reached only sessions that were still growing —
  99.7% of them would have kept their old parsing for good.
- Quote any search term FTS5 would read as syntax, not just hyphenated ones.
  `recall.py`, `CI/CD`, `v1.2.3` and `don't` each failed outright with a
  syntax error and reported no matches.
- Survive a timestamp that is a number but not a finite one. It raised out of
  the parser, past the commit, and discarded the whole run's work.
- Refuse `--limit 0` and below. SQLite read it as no limit and the results were
  then sliced from the wrong end.
- Drop the lock-file mtime signal. It let a run skip indexing when another had
  finished a scan that started before the file it needed appeared. Re-reading
  the sessions table after taking the lock answers the same question honestly,
  and takes the mechanism out of the code.

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
