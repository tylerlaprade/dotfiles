---
name: handoff
description: End-of-day handoff. /handoff captures every in-flight item from this session into a file so the session can end safely; /handoff pickup restores one into a fresh session.
argument-hint: "[pickup] [slug]"
disable-model-invocation: true
---

One job: after this session ends, no pending work is lost. A handoff is not
documentation; durable decisions belong in the repo, not here. Open items
are unfinished work, not dropped requests or those decisions.

# Write mode: `/handoff [slug]`

Harvest pending work into numbered open items:

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
- Scratchpad files that still matter: copy them into the handoff's
  subdirectory (see below).
- If this session picked up a handoff, carry its still-open items forward
  and delete that handoff. Note if the transcript was compacted.

Also record, as context rather than items: findings produced here but
recorded nowhere, and dead ends already tried.

On a long or compacted session, also read back this session's own
transcript from disk (the recall skill documents where each agent keeps
them) and check its user messages against the harvest — early asks fall
out of attention first.

Write to `<repo>/.handoffs/<yyyy-mm-dd-HHMM>-<slug>.md`, where `<repo>` is
the directory containing `git rev-parse --git-common-dir` (worktree-safe).
The handoff lives in the repo it is about, so it travels with the repo to
the next session or machine. Outside a repo, write to
`~/.agents/handoffs/<cwd basename>/` instead and say it does not travel.

- Slug from the argument or the task: lowercase, hyphens, at most 40 chars.
  Never overwrite — suffix `-2`, `-3` if the path exists.
- Header: date, cwd, repo root, origin URL, branch, HEAD, and the path to
  this session's transcript — the handoff is a summary, and the transcript
  is where a reader recovers anything it left out. For each uncommitted
  file, one line saying what the change is.
- Scratchpad copies go in a sibling directory named by the file's stem.
  Copy only what the next session needs; a large log stays where it is,
  named by path.
- Name any other open handoffs for this project.
- Write it as the repo's readers may see it: in a public repo it is
  public, so no secrets, private messages, or personal data.
- Repo state goes in as pointers only. Commit the handoff and its sibling
  directory alone, staging nothing else, and push, so another session or
  machine finds it with a pull.

Reply with the numbered open items and the file path. If nothing is in
flight, say so and write nothing.

# Pickup mode: `/handoff pickup [slug]`

- Fetch, then list `.handoffs/` as the upstream branch has it (`git pull
  --ff-only` when the tree allows, else read through `git show`). With
  several open handoffs, take the slug or ask; read all that overlap
  before acting.
- Also list `~/.agents/handoffs/<repo basename>/`, where handoffs were
  written before 2026-10-09. Close out one found there by deleting it
  there. Remove this step once that folder holds no open handoff.
- Skip loudly any handoff whose recorded repo root does not match the current
  one.
- Verify live-state claims against the repo and flag drift. A file now clean
  may have been committed — check `git log` before assuming loss.
- Reply with the unfinished work you now own, and the path you will delete
  when that work is gone.

# Close-out

This skill is not a mode that stays loaded. After pickup, close-out is the
duty named in that reply. A handoff is read, never added to: progress goes
in the work itself and in the reports, not back into the file. When that
unfinished work is gone, delete the handoff and its sibling directory,
commit the deletion alone, and push, without being asked. Leave it in
place while work remains. A later `/handoff` deletes it if leftovers are
carried into a new file. Never delete an un-picked-up handoff.
