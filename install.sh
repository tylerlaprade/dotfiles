#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing dotfiles..."

# Brew packages
brew install helix zellij ghostty alacritty zoxide direnv sd fnm pure \
  eza bat fd dust bottom procs ripgrep git-delta

# Global language servers
bun i -g typescript-language-server vscode-langservers-extracted

# Zellij plugin
mkdir -p ~/.config/zellij/plugins
curl -L "https://github.com/dj95/zjstatus/releases/latest/download/zjstatus.wasm" \
  -o ~/.config/zellij/plugins/zjstatus.wasm

# Symlinks
ln -sf $DOTFILES/.zshrc ~/.zshrc
ln -sf $DOTFILES/helix ~/.config/helix
ln -sf $DOTFILES/zellij/layouts ~/.config/zellij/layouts
mkdir -p ~/.config/alacritty
ln -sf $DOTFILES/alacritty.toml ~/.config/alacritty/alacritty.toml
mkdir -p ~/Library/Application\ Support/com.mitchellh.ghostty
ln -sf $DOTFILES/ghostty.config ~/Library/Application\ Support/com.mitchellh.ghostty/config
ln -sf $DOTFILES/.claude/statusline.sh ~/.claude/statusline.sh

echo "Done! Restart your shell."
