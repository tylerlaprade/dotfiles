#!/bin/bash
# Reclaim regenerable caches. Does not cargo-clean — cargo-sweep owns unused
# target dirs with a 14-day window. Triggered weekly by
# ~/Library/LaunchAgents/com.tylerlaprade.prune-caches.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

echo "=== $(date) ==="

if command -v brew >/dev/null; then
  brew cleanup -q --prune=14 || true
fi

if command -v uv >/dev/null; then
  uv cache prune || true
fi

# Gone devices/runtimes only. Installed runtimes (FondlyTest / iOS 26) stay.
if command -v xcrun >/dev/null; then
  xcrun simctl delete unavailable || true
fi

# Tree-sitter git checkouts are build inputs; Helix loads the .dylibs.
rm -rf "$HOME/Code/helix/runtime/grammars/sources"

if command -v rustup >/dev/null; then
  rustup set profile minimal >/dev/null
  rustup component remove rust-docs >/dev/null 2>&1 || true
fi

# Native Claude Code installer keeps every version. Keep the two newest.
versions="$HOME/.local/share/claude/versions"
if [ -d "$versions" ]; then
  old=$(ls -1 "$versions" | sort -t. -k1,1n -k2,2n -k3,3n | awk -v keep=2 ' { n[++c]=$0 } END { for (i=1;i<=c-keep;i++) print n[i] }')
  if [ -n "$old" ]; then
    echo "$old" | while IFS= read -r v; do
      rm -rf "$versions/$v"
      echo "Removed claude version $v"
    done
  fi
fi

# fnm is the Node manager; leftover nvm trees do not come back unless reinstalled.
if command -v fnm >/dev/null && [ -d "$HOME/.nvm" ]; then
  rm -rf "$HOME/.nvm"
  echo "Removed leftover ~/.nvm"
fi
