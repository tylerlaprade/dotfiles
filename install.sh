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

# 4. Brew packages
# Use experimental wrapper only if --experimental flag is passed
if [[ " $* " == *" --experimental "* ]] && { command -v wax &>/dev/null || command -v zb &>/dev/null; }; then
  source "$DOTFILES/scripts/brew-wrapper.sh"
fi
echo "Installing brew packages..."
brew bundle --file="$DOTFILES/Brewfile" --no-lock

# 5. Cargo crates (cargo-only tools not in brew)
if command -v cargo &>/dev/null; then
  echo "Installing cargo tools..."
  cargo install cargo-binstall 2>/dev/null || true
  cargo binstall -y cargo-insta cargo-workspaces 2>/dev/null || true
fi

# 6. Bun (no brew formula available)
if ! command -v bun &>/dev/null; then
  echo "Installing bun..."
  curl -fsSL https://bun.sh/install | bash
fi

# 7. Sourcery LSP (for Helix/editors)
pip3 install --user sourcery-cli 2>/dev/null || true

# 8. Remove macOS bloat
for app in GarageBand iMovie Keynote Numbers Pages; do
  [[ -d "/Applications/$app.app" ]] && sudo rm -rf "/Applications/$app.app"
done

# 9. Symlink dotfiles
echo "Syncing dotfiles..."
"$DOTFILES/scripts/sync-dotfiles.sh"

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
