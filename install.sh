#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dotfiles Setup ==="

# 1. Homebrew (needed for Xcode CLT + git, and as fallback)
if ! command -v brew &>/dev/null; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 2. Rust toolchain (needed before wax)
if ! command -v rustup &>/dev/null; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

# 3. Experimental package managers (wax + zerobrew)
if command -v cargo &>/dev/null && ! command -v wax &>/dev/null; then
  echo "Installing wax..."
  cargo install waxpkg 2>/dev/null || true
fi
if ! command -v zb &>/dev/null; then
  echo "Installing zerobrew..."
  curl -fsSL https://zerobrew.rs/install | bash 2>/dev/null || true
  [[ -f "$HOME/.local/bin/env" ]] && . "$HOME/.local/bin/env"
fi

# 4. Everything else in parallel
LOGDIR="${TMPDIR:-/tmp}/dotfiles-install-$$"
mkdir -p "$LOGDIR"

# Use experimental wrapper only if --experimental flag is passed
if [[ " $* " == *" --experimental "* ]] && { command -v wax &>/dev/null || command -v zb &>/dev/null; }; then
  source "$DOTFILES/scripts/brew-wrapper.sh"
fi

echo "Installing in parallel..."

# Brew packages (slowest — runs in background)
echo "  [brew] starting..."
(brew bundle --file="$DOTFILES/Brewfile" >"$LOGDIR/brew.log" 2>&1 && echo "  [brew] done" || echo "  [brew] FAILED — see $LOGDIR/brew.log") &
pid_brew=$!

# Cargo crates
if command -v cargo &>/dev/null; then
  echo "  [cargo] starting..."
  (cargo install cargo-binstall 2>/dev/null; cargo binstall -y cargo-insta cargo-workspaces 2>/dev/null; echo "  [cargo] done") &
  pid_cargo=$!
fi

# Bun
if ! command -v bun &>/dev/null; then
  echo "  [bun] starting..."
  (curl -fsSL https://bun.sh/install | bash >"$LOGDIR/bun.log" 2>&1 && echo "  [bun] done" || echo "  [bun] FAILED") &
  pid_bun=$!
fi

# Sourcery
echo "  [sourcery] starting..."
(pip3 install --user sourcery-cli >"$LOGDIR/sourcery.log" 2>&1 && echo "  [sourcery] done" || echo "  [sourcery] FAILED") &
pid_sourcery=$!

# Remove macOS bloat (fast, no network)
for app in GarageBand iMovie Keynote Numbers Pages; do
  [[ -d "/Applications/$app.app" ]] && sudo rm -rf "/Applications/$app.app"
done

# Wait for background jobs
wait $pid_brew 2>/dev/null
[[ -n "${pid_cargo:-}" ]] && wait $pid_cargo 2>/dev/null
[[ -n "${pid_bun:-}" ]] && wait $pid_bun 2>/dev/null
wait $pid_sourcery 2>/dev/null

# Symlink dotfiles (needs uv from brew)
echo "Syncing dotfiles..."
"$DOTFILES/scripts/sync-dotfiles.sh"

rm -rf "$LOGDIR"

echo ""
echo "=== Next steps ==="
echo "  1. Restore backup:   $DOTFILES/scripts/restore.sh ~/Desktop/machine-backup.zip"
echo "  2. macOS defaults:   $DOTFILES/scripts/apply-macos-defaults.py"
echo "  3. GitHub CLI auth:  gh auth login"
echo "  4. Sourcery auth:    sourcery login"
echo "  5. Kanata:           Grant accessibility permissions in System Preferences"
echo "  6. Karabiner:        Grant input monitoring permissions in System Preferences"
echo ""
echo "Done! Restart your shell."
