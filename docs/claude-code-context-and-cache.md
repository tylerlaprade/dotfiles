# Claude Code context, compaction, and cache

This records how Claude Code's compaction and prompt cache behave on this Mac,
which settings `.claude/settings.json` uses because of it, which options were
weighed and left off, and a design for a cache keepalive that was not built.
The measurements come from local transcripts for September 9 to October 9,
2026. The code findings come from the installed Claude Code bundle, versions
2.1.295 and 2.1.296. Several of the settings are undocumented, so recheck them
against the installed bundle under `~/.local/share/claude/versions/` before
relying on them after an upgrade.

Dollar figures are API list prices. The subscription does not bill them, so
they serve only as relative weights between sessions and policies. Whether the
weekly limit weighs tokens the same way is not known.

## Settings in use

`autoCompactWindow` is `833000`. Claude Code compacts when the context reaches
the window minus a 20,000-token output reserve and a 13,000-token summary
reserve, so this window compacts at exactly 800,000 tokens. Both reserves are
constants. `CLAUDE_CODE_MAX_OUTPUT_TOKENS` can only shrink the output reserve,
and only by capping every reply. No setting reads into the summary reserve.
In practice here, Opus 5.5 noticeably slips past roughly 600k tokens, so the
threshold sits below the default of about 967k without compacting long tasks
early. The statusline
mirrors the same formula.

`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` is `600000`. Idle compaction summarizes a
session while it waits, before its one-hour cache expires. It fires at 90% of
the cache lifetime, about 54 minutes after the last request, and only when the
cache uses the one-hour TTL, is still warm, no newer request exists, the
context is at least this size, and the account is not near its usage limit.
The default minimum is 200k and the floor is 100k. Its compaction records say
`"trigger":"manual"` or `"trigger":"auto"`, depending on the version; they
stand out only by firing about 55 minutes after the last reply and below the
threshold. Claude Code reads the `env` block when a process starts, so a
running session keeps the value it launched with: on October 9, three sessions
launched October 2 still idle-compacted at 318k to 397k tokens, below the 500k
minimum committed on October 7. Restart a session to pick up a changed
minimum, hint threshold, or floor. Its summaries
capture the session at the moment it handed back to the user, so they tend to
read as "report and wait," and the resumed session can seem forgetful and keen
to wait for approval. `"idleCompaction": false` turns it off entirely.

`CLAUDE_CODE_IDLE_THRESHOLD_MINUTES` is `60` and
`CLAUDE_CODE_IDLE_TOKEN_THRESHOLD` is `350000`. Together they set when the
`new task? /clear to save N tokens` hint appears: after this much idle time, in
sessions at least this large. The defaults are 75 minutes and 100k tokens. At
75 minutes the hint stays silent while most messages already pay a full cache
rebuild (see the table below). At 350k it flags about 2.6 resumes a day, each
rebuilding at least $2.80 of context on Opus 5.5. Sessions over 600k are
usually idle-compacted before the hint can appear. The 75-minute default is
reported upstream as
[anthropics/claude-code#100894](https://github.com/anthropics/claude-code/issues/100894).

## Cache behavior observed

The prompt cache stores the request's prompt, not the reply; a reply is written
to the cache when the next request carries it. Anthropic's docs measure the
lifetime from the start of the request. Measured from the end of the previous
reply, main-thread prompts with over 50k tokens of context hit the cache as
follows:

| Idle minutes | Prompts | Cache hits |
|---|---|---|
| 45–60 | 47 | 100% |
| 60–62 | 5 | 40% |
| 62–65 | 10 | 30% |
| 65–75 | 11 | 9% |

Nothing expired before 60 idle minutes, and a few prompts still hit at 60–65
minutes even when measured from the previous request's start. Either the TTL
has a few minutes of slack or some request outside the transcript refreshes
it.

Cache reads on Opus 5.5 cost $0.20 per million tokens, 5% of input, and a
one-hour cache write costs $8 per million. Even so, cache reads were 54% of the
month's API-equivalent cost, because long sessions re-read 400k to 900k tokens
on every call. 60% of the cost came from calls carrying more than 400k tokens
of context. Rebuilding a session's cache after more than an hour idle happened
289 times, at about 476k tokens each. Wakeups from background jobs and other
sessions were about 12% of the cost, almost all of it on a warm cache; only 15
wakeups in the month landed on an expired one.

## Options weighed and left off

Turning idle compaction off keeps full context across long breaks at the cost
of a cache rebuild on return. It stays on, with the 600k minimum, so only the
largest sessions are summarized while idle.

The full 1M window, compacting at about 967k, would let a long task run past
800k and leave the natural-pause compaction to idle compaction. Claude Code
has no setting that waits for a turn boundary once past a soft limit; it
checks one threshold before every request, mid-task included. The full window
was rejected because the extra room lies where Opus 5.5 slips. Mid-task
compaction is less disruptive than it sounds: with background precompute on,
the switch takes about 50 milliseconds and keeps a block of recent messages
verbatim. The minified code appears to prepare the summary at about 80% of the
window minus the output reserve and carry everything after that point over
verbatim, but that reading was not confirmed.

A larger compaction reserve would not improve the summary. The reserve only
leaves room for the compaction request; the summary's length is capped
separately. Compacting earlier may help indirectly, because there is less to
compress and the summarizer reads less context.

`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` sets the threshold as a percentage of the
window minus the output reserve and can only lower it; a plain window size is
more direct. `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` moves the hard stop near the
context limit, not the compaction point. `precomputeCompactionEnabled` is
already on by default and should stay on. `CLAUDE_CODE_SLEEP_COMPACT` and
`CLAUDE_CODE_COLD_COMPACT` appear in the compaction code, but their effect
could not be read from the minified source, so they stay unset.

`CLAUDE_CODE_LOOP_KEEPALIVE` is not a cache keepalive. Inside a self-paced
`/loop`, it arms a 20-minute fallback wakeup when the model ends a turn without
scheduling the next one, and ends the loop if the model declines again. It is
on by default through a server flag. A self-paced `/loop` can wake as often as
every minute or as rarely as every 60, so a session babysitting a job longer
than an hour can check in under the cache lifetime without new machinery.

Capping every background wait at about 40 minutes would add turns to save
almost nothing: wakeups rarely land cold, and Monitor watches already expire
and wake the session every 30 minutes at most. Batching monitor events cuts
wakeup cost more, because each event is a full-context turn whether the cache
is warm or not.

Laminar's Claude Code and Codex plugins were evaluated for cross-session
visibility and rejected. They upload each turn only when it ends, so a runaway
turn is invisible until it stops. They see token counts, not subscription
quota. Transcripts here run about 300 MB a week, so the free tier's 1 GB
would run out within about a month if it counts monthly ingestion, which
Laminar's pricing page does not say. PII redaction is paid-only.

## Keepalive design

A keepalive would preserve quota, not memory. A cache rebuild costs quota but
loses nothing; only compaction loses context. It is worth building only if the
weekly subscription limit starts to bind.

It would send a short real turn to each session idle and waiting on the user,
every 50 minutes, so the one-hour cache never expires. The ping has to be a
real request carrying the session's context, so it lands in the transcript as
a short message and reply that the model sees. Measured from short warm turns,
each ping writes about 355 new tokens to the cache, produces about 545 output
tokens including thinking, and adds about 900 tokens to the context. On a 476k
session, that is $0.095 of cache reads plus $0.014 of writing and output. A
rebuild of the same session costs about $3.80, so one rebuild equals about 40
pings, or 33 hours of pinging.

Every session is eventually resumed, so the stop rule only trades pings
against rebuilds on long breaks. Simulated against the month's 289 idle
resumes, repriced at Opus 5.5 rates and including ping overhead:

| Policy | Pings | Rebuilds | Total |
|---|---|---|---|
| No keepalive | $0 | $1,072 | $1,072 |
| Ping every 50 minutes, never stop | $468 | $0 | $468 |
| Stop after 24 hours idle | $299 | $122 | $421 |
| Stop at 11 PM once idle 24 hours | $316 | $120 | $436 |
| Stop after 33 hours idle | $337 | $116 | $453 |
| Stop after 12 hours idle | $219 | $298 | $517 |

Pausing every night from 11 PM is worse than any of these, about $644 at the
month's mixed-model prices: a night of pings costs about $1.20, while the
morning rebuild it causes costs about $4.50. The night start time barely
matters once the rule waits for 24 idle hours; 9 PM, 10 PM, 11 PM, and
midnight landed within $5 of each other. Quiet hours here start around 11 PM.

A keepalive would skip sessions that are busy, have a pending `/loop` wakeup,
or are waiting on a background job, since those wake on their own. A sleeping
Mac sends nothing, so overnight pings happen only while it stays awake.
Whether a ping resets idle compaction's 54-minute timer was not checked; idle
compaction should be off or its minimum above the session size for a
keepalive to be the only thing acting on idle sessions.

## Measuring again

Claude Code writes one transcript line per content block, so one API response
spans several lines. Main-thread lines repeat the final usage, but subagent
lines carry partial output counts on all but the last, so take the last line
for each `message.id`. Forked sessions copy their parent's history, so
deduplicate message IDs across files. Subagent transcripts live under
`<session>/subagents/` beside each session file, with a `.meta.json` naming
the agent type and model. ccusage reads these correctly and is the quick
cross-check for totals.
