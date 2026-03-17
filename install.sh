#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dotfiles Setup ==="

# 1. Homebrew
if ! command -v brew &>/dev/null; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 2. Brew packages
echo "Installing brew packages..."
brew bundle --file="$DOTFILES/Brewfile" --no-lock

# 3. Rust toolchain
if ! command -v rustup &>/dev/null; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

# 4. Cargo crates (cargo-only tools not in brew)
if command -v cargo &>/dev/null; then
  echo "Installing cargo tools..."
  cargo install cargo-binstall 2>/dev/null || true
  cargo binstall -y cargo-insta cargo-workspaces 2>/dev/null || true
fi

# 5. Bun (no brew formula available)
if ! command -v bun &>/dev/null; then
  echo "Installing bun..."
  curl -fsSL https://bun.sh/install | bash
fi

# 6. Sourcery LSP (for Helix/editors)
pip3 install --user sourcery-cli 2>/dev/null || true

# 7. Remove macOS bloat
for app in GarageBand iMovie Keynote Numbers Pages; do
  [[ -d "/Applications/$app.app" ]] && sudo rm -rf "/Applications/$app.app"
done

# 8. Symlink dotfiles
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
