#!/bin/bash
# Prune Rust build artifacts unused for 14+ days across ~/Code, and cap
# QueenspawnGames' shared cache. Cargo has no target-dir GC (rust-lang/cargo#13136).
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.cargo-sweep.plist.
set -euo pipefail

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

QG="$HOME/Code/QueenspawnGames"

echo "=== $(date) ==="
# --hidden: QG keeps its cache in a dot-dir, which recursive mode skips by default.
cargo sweep --recursive --hidden --time 14 "$HOME/Code"
# The recursive pass only finds dirs literally named "target"; QG's .cargo-target
# needs the env var to be swept at all.
CARGO_TARGET_DIR="$QG/.cargo-target" cargo sweep --time 14 "$QG"
CARGO_TARGET_DIR="$QG/.cargo-target" cargo sweep --maxsize 8GB "$QG"
