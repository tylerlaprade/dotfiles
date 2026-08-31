#!/bin/bash
# Pull gj1118/helix master and rebuild hx if the installed binary is behind.
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.update-helix.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="$HOME/Code/helix"

echo "=== $(date) ==="
cd "$REPO"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree dirty; skip"
  exit 0
fi

git fetch --quiet origin master
after=$(git rev-parse origin/master)

if [[ -n "$(git rev-list -n 1 HEAD --not origin/master)" ]]; then
  echo "HEAD has commits not on origin/master; skip"
  exit 0
fi

if [[ "$(git branch --show-current)" != "master" ]]; then
  echo "Leaving $(git branch --show-current) for master"
  git checkout master
fi
git merge --ff-only origin/master

installed=$(hx --version 2>/dev/null | sed -n 's/.*(\([0-9a-f][0-9a-f]*\)).*/\1/p' || true)
if [[ -n "$installed" ]] && git rev-parse --verify "${installed}^{commit}" >/dev/null 2>&1 \
  && [[ "$(git rev-parse "${installed}^{commit}")" == "$after" ]]; then
  echo "hx already at $installed"
  exit 0
fi

echo "Rebuilding ${installed:-none} -> $(git rev-parse --short "$after")"
# 8 GB machine: unlimited rustc jobs with LTO will swap the box to death.
CARGO_BUILD_JOBS=2 cargo install --path helix-term --locked --force
# Grammar git checkouts are build inputs; Helix loads the .dylibs.
rm -rf "$REPO/runtime/grammars/sources"
echo "Rebuilt hx: $(hx --version)"
