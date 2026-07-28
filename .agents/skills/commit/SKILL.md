---
name: commit
description: Stage and commit current changes without touching unrelated files. Triggers on /commit.
disable-model-invocation: true
---

# Commit only your work

Please STAGE (in hunks with `git add -p`, if needed) and commit your changes.
Don't try to stash, reset, checkout, or otherwise touch any unrelated changes.
Commit normally so configured hooks can run. Afterward, verify the exact
committed diff and final working tree status. Report any hook changes or
warnings.
