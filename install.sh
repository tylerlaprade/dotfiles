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

# 4. Cargo crates (via binstall for speed)
if command -v cargo &>/dev/null; then
  echo "Installing cargo tools..."
  cargo install cargo-binstall 2>/dev/null || true
  cargo binstall -y cargo-insta cargo-nextest cargo-workspaces just taplo-cli wasm-pack wasm-tools 2>/dev/null || true
fi

# 5. Bun
if ! command -v bun &>/dev/null; then
  echo "Installing bun..."
  curl -fsSL https://bun.sh/install | bash
fi

# 6. uv
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# 7. Global bun packages
echo "Installing global bun packages..."
bun i -g typescript-language-server vscode-langservers-extracted bash-language-server yaml-language-server markdownlint-cli2 2>/dev/null || true

# 8. Sourcery LSP (for Helix/editors)
pip3 install --user sourcery-cli 2>/dev/null || true

# 9. Symlink dotfiles
echo "Syncing dotfiles..."
"$DOTFILES/scripts/sync-dotfiles.sh"

# 10. VS Code extensions (from Brewfile vscode entries)
if command -v code &>/dev/null; then
  echo "Installing VS Code extensions..."
  brew bundle --file="$DOTFILES/Brewfile" --no-lock --vscode 2>/dev/null || true
fi

echo ""
echo "=== Manual steps remaining ==="
echo "  1. macOS defaults:  $DOTFILES/scripts/macos-defaults.sh"
echo "  2. GPG key import:  gpg --import /path/to/key.asc"
echo "  3. SSH key:          Copy ~/.ssh/id_ed25519{,.pub} from backup"
echo "  4. Graphite auth:    gt auth"
echo "  5. GitHub CLI auth:  gh auth login"
echo "  6. Sourcery auth:    sourcery login"
echo "  7. VS Code Sourcery: Run 'Sourcery: Login' from command palette"
echo "  8. Kanata:           Grant accessibility permissions in System Preferences"
echo "  9. Karabiner:        Grant input monitoring permissions in System Preferences"
echo ""
echo "Done! Restart your shell."
