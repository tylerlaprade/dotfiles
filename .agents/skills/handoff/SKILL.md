---
name: handoff
description: End-of-day handoff. /handoff captures every in-flight item from this session into a file so the session can end safely; /handoff pickup restores one into a fresh session.
argument-hint: [pickup] [slug]
disable-model-invocation: true
---

One job: after this session ends, no pending work is lost. A handoff is not
documentation; durable decisions belong in the repo, not here.

# Write mode: `/handoff [slug]`

Harvest what is pending and what only this session knows:

- User requests not yet delivered — including hedged ones and unanswered
  questions. Quote the user's words where nuance matters.
- Work that runs beyond this session: background tasks, monitors, servers, cloud
  or peer agents (what each was asked, where its output lands), scheduled
  jobs, CI being watched, artifact or PR threads awaiting replies, a browser
  left mid-flow.
- State git does not show: worktrees and stashes made here, files changed
  outside the repo and live copies still to sync back, migrations or external
  systems touched, env or config the next session must set up again.
- Temporary edits that must come out before landing: skipped tests, debug
  logging, hardcoded values, disabled checks.
- Findings produced here but recorded nowhere, and dead ends already tried.
- Scratchpad files that still matter: copy them into the handoff's
  subdirectory (see below).
- If this session picked up a handoff, carry its still-open items forward.
  Note if the transcript was compacted.

On a long or compacted session, also read back this session's own
transcript from disk (the recall skill documents where each agent keeps
them) and check its user messages against the harvest — early asks fall
out of attention first.

Write to `~/.agents/handoffs/<project>/<yyyy-mm-dd-HHMM>-<slug>.md`:

- `<project>`: basename of the directory containing `git rev-parse
  --git-common-dir` (worktree-safe); outside a repo, the cwd basename.
- Slug from the argument or the task: lowercase, hyphens, at most 40 chars.
  Never overwrite — suffix `-2`, `-3` if the path exists.
- Header: date, cwd, repo root, origin URL, branch, HEAD. For each
  uncommitted file, one line saying what the change is.
- Scratchpad copies go in a sibling directory named by the file's stem.
- Name any other open handoffs for this project.
- Repo state goes in as pointers only — never stage or commit anything.

Reply with the numbered open items and the file path. If nothing is in
flight, say so and write nothing.

# Pickup mode: `/handoff pickup [slug]`

- List `~/.agents/handoffs/<project>/`. With several open handoffs, take the
  slug or ask; read all that overlap before acting.
- Skip loudly any handoff whose recorded repo root does not match the current
  one.
- Verify live-state claims against the repo and flag drift. A file now clean
  may have been committed — check `git log` before assuming loss.
- Reply with the open items you now own.
- Move a handoff to `done/` only when its items are finished or carried into
  a new handoff — never on reading. Never delete an un-picked-up handoff;
  entries in `done/` older than 30 days may be deleted.
