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

# Protect tracked shell configs from installers that append to them
shell_configs=("$HOME/.zshrc" "$HOME/.zshenv" "$HOME/.zprofile")
for f in "${shell_configs[@]}"; do
  [[ -f "$f" || -L "$f" ]] && chmod a-w "$f" 2>/dev/null || true
done

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

# Sourcery (AI code review — "sourcery" not "sourcery-cli", which is stale/x86-only)
echo "  [sourcery] starting..."
(uv tool install --force sourcery >"$LOGDIR/sourcery.log" 2>&1 && echo "  [sourcery] done" || echo "  [sourcery] FAILED") &
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

# Node via fnm (needed for Codex CLI)
if command -v fnm &>/dev/null; then
  eval "$(fnm env)" 2>/dev/null
  if ! fnm list 2>/dev/null | grep -q default; then
    echo "Installing Node LTS..."
    fnm install --lts
    fnm default lts-latest
    eval "$(fnm env)" 2>/dev/null
  fi
fi

# Codex CLI (needs node from fnm)
if ! command -v codex &>/dev/null && command -v npm &>/dev/null; then
  echo "Installing Codex CLI..."
  npm i -g @openai/codex 2>/dev/null || true
fi

# Restore write permissions on shell configs
for f in "${shell_configs[@]}"; do
  [[ -f "$f" || -L "$f" ]] && chmod u+w "$f" 2>/dev/null || true
done

# GitHub CLI auth (needed for release downloads below)
if ! gh auth status &>/dev/null; then
  echo "GitHub CLI auth required for app downloads..."
  gh auth login
fi

# Graphite desktop app (not in Homebrew)
if [[ ! -d "/Applications/Graphite.app" ]]; then
  echo "Installing Graphite desktop app..."
  gh release download --repo withgraphite/graphite-desktop --pattern '*darwin-arm64*' -D /tmp --clobber 2>/dev/null \
    && unzip -qo /tmp/Graphite-darwin-arm64-*.zip -d /Applications/ 2>/dev/null \
    && rm /tmp/Graphite-darwin-arm64-*.zip \
    && echo "  Graphite installed" \
    || echo "  Graphite install failed"
fi

# Notchi (Claude Code notch companion, not in Homebrew)
if [[ ! -d "/Applications/Notchi.app" ]]; then
  echo "Installing Notchi..."
  gh release download --repo sk-ruban/notchi --pattern '*.dmg' -D /tmp --clobber 2>/dev/null \
    && hdiutil attach /tmp/Notchi-*.dmg -nobrowse -quiet \
    && cp -R "/Volumes/Notchi/Notchi.app" /Applications/ \
    && hdiutil detach "/Volumes/Notchi" -quiet \
    && rm /tmp/Notchi-*.dmg \
    && echo "  Notchi installed" \
    || echo "  Notchi install failed"
fi

# Remove files that block symlink creation (created by tools or restore)
for f in .gitconfig .npmrc; do
  [[ -f "$HOME/$f" && ! -L "$HOME/$f" ]] && rm "$HOME/$f"
done
for f in gpg.conf gpg-agent.conf dirmngr.conf; do
  [[ -f "$HOME/.gnupg/$f" && ! -L "$HOME/.gnupg/$f" ]] && rm "$HOME/.gnupg/$f"
done

# Symlink dotfiles (needs uv from brew)
# Skip macOS defaults capture on install — we want to apply, not overwrite
echo "Syncing dotfiles..."
SKIP_DEFAULTS_SYNC=1 "$DOTFILES/scripts/sync-dotfiles.sh"

# Keep logs around for inspection — they're in $TMPDIR and will be cleaned by the OS
echo "  Install logs: $LOGDIR"

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

# /etc/hosts
if ! grep -q "local.paqarina.dev" /etc/hosts 2>/dev/null; then
  sudo sh -c 'echo "127.0.0.1       local.paqarina.dev" >> /etc/hosts'
fi

echo ""
echo "=== Next steps ==="
echo "  1. Sourcery auth:    sourcery login"
echo "  2. Kanata:           Grant accessibility permissions in System Preferences"
echo "  3. Karabiner:        Grant input monitoring permissions in System Preferences"
echo ""
echo "Done! Restart your shell."
