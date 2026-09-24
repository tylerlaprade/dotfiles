# Semantic re-rank for recall (deferred)

recall is BM25-only. dirsql's `dirsql-plugin-embeddings` shows the cheap
local path to semantic search: model2vec static embeddings (numpy +
tokenizers, no torch, ~100MB one-time download), a worker spawned lazily,
vectors cached by content hash. This note is the shape of that idea fitted
to recall, for whenever it earns its complexity.

## Proposal

Keep BM25 as the first pass. Embed only the candidate set at query time and
re-rank semantically. Zero indexing cost, no torch dependency, nothing
changes for callers who don't ask for it.

## Shape

- Opt-in per query (a `--semantic` flag, not a config file and not automatic):
  the first semantic query pays a ~100MB model download and a cold start, so
  it must be the caller's choice, never a surprise.
- At query time, take the BM25 candidates (the existing over-fetched set),
  embed each candidate's message texts with model2vec, cosine-score them
  against the embedded query, and blend with the BM25 rank the way the
  recency boost already blends — one more multiplicative term, same pattern.
- Cache vectors by SHA-256 of the message text plus the model id, in a
  `vectors` table beside the index or a sidecar cache db with the same 0600
  permissions. A cache hit means no recompute; changing the model id
  invalidates the cache without a migration.
- The worker (model load + embedding) lives behind the flag: no import, no
  download, no memory cost when a query doesn't ask for it — the same
  laziness as dirsql's plugin.
- Empty or unreadable texts are skipped loudly, not embedded as zeros: a
  zero vector is close to everything and would corrupt the ranking silently.

## Non-goals

- Not a replacement for BM25, and not embeddings at index time. Index-time
  embeddings would tax every run for a feature most queries don't use, and
  would invalidate on every model change.
- No torch, no sentence-transformers, no network calls at query time. If it
  needs the network past the one-time model download, the design is wrong.

## Contract

Exit codes unchanged. Flag off means byte-identical behavior to today —
that is the test that matters most.

## Tests to add

- A query whose keywords miss but whose meaning hits: the semantically
  related session moves up under `--semantic` and stays put without it.
- Second identical query hits the vector cache (no recompute).
- `--semantic` with an empty candidate set exits 1, not 4 and not a
  traceback.

## Open questions

- Which model2vec model (multilingual? code-aware?). Measure on real
  sessions, don't reason from model cards.
- Cache inside `~/.recall.db` or a sidecar. Inside keeps one file to back
  up and permission; a sidecar keeps the FTS index lean. Undecided.
