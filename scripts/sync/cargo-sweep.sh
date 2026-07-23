#!/bin/bash
# Prune Rust build artifacts unused for 14+ days across ~/Code. Cargo has no
# target-dir GC (rust-lang/cargo#13136).
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.cargo-sweep.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

QG="$HOME/Code/QueenspawnGames"

echo "=== $(date) ==="
for dir in "$HOME"/Code/*/; do
  case "$(basename "$dir")" in
    # train-game runs nightly from its prebuilt binary; running an artifact
    # never touches its mtime, so sweep would misread the whole tree as stale
    # and force a rebuild.
    train-game) continue ;;
  esac
  # --hidden: QG keeps its cache in a dot-dir, which recursive mode skips by default.
  cargo sweep --recursive --hidden --time 14 "$dir"
done
# The recursive pass only finds dirs literally named "target"; QG's .cargo-target
# needs the env var to be swept at all. Time-based only — no size cap, so
# artifacts every full build still needs are never evicted.
CARGO_TARGET_DIR="$QG/.cargo-target" cargo sweep --time 14 "$QG"
