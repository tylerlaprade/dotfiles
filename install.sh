#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing dotfiles..."

# Brew formulae
brew install helix zellij zoxide direnv sd fnm pure \
  eza bat fd dust bottom procs ripgrep git-delta

# Brew casks (skip if already installed)
brew install --cask ghostty alacritty 2>/dev/null || true

# Global language servers
bun i -g typescript-language-server vscode-langservers-extracted

# Symlinks
"$DOTFILES/scripts/sync-dotfiles.sh"

echo "Done! Restart your shell."
