#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

link() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    ln -snf "$src" "$dst"
  elif [[ -e "$dst" ]]; then
    echo "Warning: $dst exists and is not a symlink, skipping"
  else
    ln -s "$src" "$dst"
  fi
}

echo "Installing dotfiles..."

# Brew formulae
brew install helix zellij zoxide direnv sd fnm pure \
  eza bat fd dust bottom procs ripgrep git-delta

# Brew casks (skip if already installed)
brew install --cask ghostty alacritty 2>/dev/null || true

# Global language servers
bun i -g typescript-language-server vscode-langservers-extracted

# Zellij plugin
mkdir -p ~/.config/zellij/plugins
curl -L "https://github.com/dj95/zjstatus/releases/latest/download/zjstatus.wasm" \
  -o ~/.config/zellij/plugins/zjstatus.wasm

# Symlinks
link $DOTFILES/.zshrc ~/.zshrc
link $DOTFILES/helix ~/.config/helix
link $DOTFILES/zellij/layouts ~/.config/zellij/layouts
mkdir -p ~/.config/alacritty
link $DOTFILES/alacritty.toml ~/.config/alacritty/alacritty.toml
mkdir -p ~/Library/Application\ Support/com.mitchellh.ghostty
link $DOTFILES/ghostty.config ~/Library/Application\ Support/com.mitchellh.ghostty/config
mkdir -p ~/.claude
link $DOTFILES/.claude/statusline.sh ~/.claude/statusline.sh
mkdir -p ~/.local/bin
link $DOTFILES/scripts/run-vscode-tasks.sh ~/.local/bin/run-vscode-tasks

echo "Done! Restart your shell."
