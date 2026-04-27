#!/bin/bash
# Pull gj1118/helix master and rebuild hx if there are new commits.
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.update-helix.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="$HOME/Code/helix"

echo "=== $(date) ==="
cd "$REPO"

before=$(git rev-parse HEAD)
git fetch --quiet origin master
after=$(git rev-parse origin/master)

if [[ "$before" == "$after" ]]; then
  echo "Up to date at $before"
  exit 0
fi

echo "Updating $before -> $after"
git merge --ff-only origin/master
cargo install --path helix-term --locked --force
echo "Rebuilt hx: $(hx --version)"
