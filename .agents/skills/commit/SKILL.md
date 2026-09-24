---
name: commit
description: Stage and commit current changes without touching unrelated files. Triggers on /commit.
disable-model-invocation: true
---

# Commit only your work

Stage and commit only the lines you wrote in this session. Check every hunk of
`git diff` against your own edits: a changed line you did not write belongs to
another session, even if it was already in the file when you first read it, so
leave it unstaged. When a file holds both, stage just your hunks, for example
by applying a patch of them with `git apply --cached`.
Don't try to stash, reset, checkout, or otherwise touch any unrelated changes.
Commit normally. Afterward, verify the exact committed diff and final
working tree status.
