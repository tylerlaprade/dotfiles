#!/bin/bash
# Pull gj1118/helix master and rebuild hx if there are new commits.
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.update-helix.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="$HOME/Code/helix"

echo "=== $(date) ==="
cd "$REPO"

branch=$(git branch --show-current)
if [[ "$branch" != "master" ]]; then
  echo "Checked out $branch, not master; skip"
  exit 0
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree dirty; skip"
  exit 0
fi

before=$(git rev-parse HEAD)
git fetch --quiet origin master
after=$(git rev-parse origin/master)

if [[ "$before" == "$after" ]]; then
  echo "Up to date at $before"
  exit 0
fi

echo "Updating $before -> $after"
git merge --ff-only origin/master
# 8 GB machine: unlimited rustc jobs with LTO will swap the box to death.
CARGO_BUILD_JOBS=2 cargo install --path helix-term --locked --force
# Grammar git checkouts are build inputs; Helix loads the .dylibs.
rm -rf "$REPO/runtime/grammars/sources"
echo "Rebuilt hx: $(hx --version)"
