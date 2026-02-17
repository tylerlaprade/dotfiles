#!/bin/bash
# Lightweight symlink sync — safe to run repeatedly.
# Called from Claude Code SessionStart hook to keep links current.

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

link() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    ln -snf "$src" "$dst"
  elif [[ -e "$dst" ]]; then
    return
  else
    ln -s "$src" "$dst"
  fi
}

# ~/.config/*
mkdir -p ~/.config
for item in "$DOTFILES"/.config/*; do
  link "$item" "$HOME/.config/$(basename "$item")"
done

# ~/.claude/* (skip machine-local files)
mkdir -p ~/.claude
for item in "$DOTFILES"/.claude/*; do
  name="$(basename "$item")"
  [[ "$name" == "settings.local.json" ]] && continue
  link "$item" "$HOME/.claude/$name"
done

# ~/.*rc, ~/.gitconfig, etc.
for item in "$DOTFILES"/.[!.]*; do
  name="$(basename "$item")"
  [[ "$name" == ".git" || "$name" == ".config" || "$name" == ".claude" ]] && continue
  link "$item" "$HOME/$name"
done

# scripts -> ~/.local/bin
mkdir -p ~/.local/bin
for script in "$DOTFILES"/scripts/*.sh; do
  link "$script" "$HOME/.local/bin/$(basename "$script" .sh)"
done
