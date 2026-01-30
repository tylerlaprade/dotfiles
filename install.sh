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
link $DOTFILES/.gitconfig ~/.gitconfig

mkdir -p ~/.config/alacritty ~/.config/ghostty ~/.config/git ~/.config/zellij
link $DOTFILES/.config/helix ~/.config/helix
link $DOTFILES/.config/alacritty/alacritty.toml ~/.config/alacritty/alacritty.toml
link $DOTFILES/.config/ghostty/config ~/.config/ghostty/config
link $DOTFILES/.config/git/ignore ~/.config/git/ignore
link $DOTFILES/.config/zellij/config.kdl ~/.config/zellij/config.kdl
link $DOTFILES/.config/zellij/layouts ~/.config/zellij/layouts

mkdir -p ~/.claude
link $DOTFILES/.claude/statusline.sh ~/.claude/statusline.sh

mkdir -p ~/.local/bin
link $DOTFILES/scripts/run-vscode-tasks.sh ~/.local/bin/run-vscode-tasks
link $DOTFILES/scripts/gh-pr-lookup.sh ~/.local/bin/gh-pr-lookup
link $DOTFILES/scripts/gh-pr-status.sh ~/.local/bin/gh-pr-status
link $DOTFILES/scripts/git-status-line.sh ~/.local/bin/git-status-line

echo "Done! Restart your shell."
