#!/bin/bash
set -e

echo "Installing dotfiles..."

# Brew packages
brew install helix zellij ghostty zoxide direnv sd fnm \
  eza bat fd dust bottom procs ripgrep git-delta

# Global language servers
bun i -g typescript-language-server vscode-langservers-extracted

# Zellij plugin
mkdir -p ~/.config/zellij/plugins
curl -L "https://github.com/dj95/zjstatus/releases/latest/download/zjstatus.wasm" \
  -o ~/.config/zellij/plugins/zjstatus.wasm

# Symlinks
ln -sf ~/Code/dotfiles/.zshrc ~/.zshrc
ln -sf ~/Code/dotfiles/helix ~/.config/helix
ln -sf ~/Code/dotfiles/zellij/layouts ~/.config/zellij/layouts

echo "Done! Restart your shell."
