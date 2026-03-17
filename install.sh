#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dotfiles Setup ==="

# Acquire sudo upfront and keep alive (used for removing bloat apps)
sudo -v
while true; do sudo -n true; sleep 50; kill -0 "$$" || exit; done 2>/dev/null &

# 1. Homebrew (needed for Xcode CLT + git, and as fallback)
if ! command -v brew &>/dev/null; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 2. Rust toolchain (needed before wax)
if ! command -v rustup &>/dev/null; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
  source "$HOME/.cargo/env"
fi

# 3. Experimental package managers (wax + zerobrew)
if command -v cargo &>/dev/null && ! command -v wax &>/dev/null; then
  echo "Installing wax..."
  cargo install waxpkg 2>/dev/null || true
fi
export PATH="$HOME/.local/bin:$PATH"
if ! command -v zb &>/dev/null; then
  echo "Installing zerobrew..."
  curl -fsSL https://zerobrew.rs/install | bash -s -- --no-modify-path 2>/dev/null || true
  export PATH="$HOME/.local/bin:$PATH"
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
(uv tool install sourcery-cli >"$LOGDIR/sourcery.log" 2>&1 && echo "  [sourcery] done" || echo "  [sourcery] FAILED") &
pid_sourcery=$!

# Claude Code CLI (native installer, auto-updates)
if ! command -v claude &>/dev/null; then
  echo "  [claude] starting..."
  (curl -fsSL https://claude.ai/install.sh | bash >"$LOGDIR/claude.log" 2>&1 && echo "  [claude] done" || echo "  [claude] FAILED") &
  pid_claude=$!
fi


# Remove macOS bloat (fast, no network)
for app in GarageBand iMovie Keynote Numbers Pages; do
  [[ -d "/Applications/$app.app" ]] && sudo rm -rf "/Applications/$app.app"
done

# Wait for background jobs
wait $pid_brew 2>/dev/null
[[ -n "${pid_cargo:-}" ]] && wait $pid_cargo 2>/dev/null
[[ -n "${pid_bun:-}" ]] && wait $pid_bun 2>/dev/null
wait $pid_sourcery 2>/dev/null
[[ -n "${pid_claude:-}" ]] && wait $pid_claude 2>/dev/null

# Codex CLI (needs node from fnm/brew, must run after brew finishes)
if ! command -v codex &>/dev/null; then
  echo "Installing Codex CLI..."
  eval "$(fnm env)" 2>/dev/null
  npm i -g @openai/codex 2>/dev/null || true
fi

# Undo shell config modifications from installers (bun has no --no-modify-path)
git -C "$DOTFILES" checkout -- .zshrc .zshenv .zprofile 2>/dev/null || true

# Symlink dotfiles (needs uv from brew)
# Skip macOS defaults capture on install — we want to apply, not overwrite
echo "Syncing dotfiles..."
SKIP_DEFAULTS_SYNC=1 "$DOTFILES/scripts/sync-dotfiles.sh"

rm -rf "$LOGDIR"

# Restore from backup if archive exists
BACKUP="$HOME/Desktop/machine-backup.zip"
if [[ -f "$BACKUP" ]]; then
  echo ""
  "$DOTFILES/scripts/restore.sh" "$BACKUP"
fi

# Apply macOS defaults
echo ""
echo "Applying macOS defaults..."
"$DOTFILES/scripts/apply-macos-defaults.py"

echo ""
echo "=== Next steps ==="
echo "  1. GitHub CLI auth:  gh auth login"
echo "  2. Sourcery auth:    sourcery login"
echo "  3. Kanata:           Grant accessibility permissions in System Preferences"
echo "  4. Karabiner:        Grant input monitoring permissions in System Preferences"
echo ""
echo "Done! Restart your shell."
