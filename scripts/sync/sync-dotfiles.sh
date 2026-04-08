#!/bin/bash
# Lightweight symlink sync — safe to run repeatedly.
# Called from Claude Code SessionStart hook to keep links current.

_source="${BASH_SOURCE[0]}"
while [[ -L "$_source" ]]; do
  _source="$(readlink "$_source")"
done
DOTFILES="$(cd "$(dirname "$_source")/../.." && pwd)"

link() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    ln -snf "$src" "$dst"
  elif [[ -e "$dst" ]]; then
    local backup="${dst}.pre-dotfiles-$(date +%Y%m%d%H%M%S)"
    mv "$dst" "$backup"
    echo "ℹ️  Backed up $dst -> $backup"
    ln -s "$src" "$dst"
  else
    ln -s "$src" "$dst"
  fi
}

# Recursively link leaf files/symlinks from src_dir into dst_dir,
# creating subdirectories as needed. This avoids replacing directories
# so programs can create their own local files alongside tracked ones.
link_tree() {
  local src_dir="${1%/}" dst_dir="$2"
  # Replace directory-level symlinks with real directories
  if [[ -L "$dst_dir" ]]; then
    rm "$dst_dir"
  fi
  mkdir -p "$dst_dir"
  for item in "$src_dir"/*; do
    [[ -e "$item" || -L "$item" ]] || continue
    local name="$(basename "$item")"
    if [[ -d "$item" && ! -L "$item" ]]; then
      link_tree "$item" "$dst_dir/$name"
    else
      link "$item" "$dst_dir/$name"
    fi
  done
}

# ~/.config/*
for dir in "$DOTFILES"/.config/*/; do
  link_tree "$dir" "$HOME/.config/$(basename "$dir")"
done

# Helix languages.toml — secret-aware bidirectional sync (has Sourcery token)
helix_lang_local="$HOME/.config/helix/languages.toml"
helix_lang_repo="$DOTFILES/.config/helix/languages.toml"
if [[ -L "$helix_lang_local" ]]; then
  rm "$helix_lang_local"
fi
"$DOTFILES/scripts/sync/sync-helix-languages.py" "$helix_lang_repo" "$helix_lang_local"

# ~/.claude/* (skip machine-local files)
mkdir -p "$HOME/.claude"
for item in "$DOTFILES"/.claude/*; do
  local_name="$(basename "$item")"
  [[ "$local_name" == "settings.local.json" ]] && continue
  if [[ "$local_name" == "plugins" ]]; then
    continue  # managed by extraKnownMarketplaces in settings.json
  elif [[ -d "$item" && ! -L "$item" ]]; then
    link_tree "$item" "$HOME/.claude/$local_name"
  else
    link "$item" "$HOME/.claude/$local_name"
  fi
done
# Normalize settings.json to match Claude Code's native JSON serializer so
# TUI setting toggles don't create formatting-only diffs.
"$DOTFILES/scripts/sync/format-claude-settings.py" "$DOTFILES/.claude/settings.json"

# ~/.codex/* (keep runtime state local)
mkdir -p "$HOME/.codex"
if [[ -f "$DOTFILES/.codex/config.toml" ]]; then
  link "$DOTFILES/.codex/config.toml" "$HOME/.codex/config.toml"
fi

# Sync only user-managed Codex skills and memories. Leave bundled `.system`
# skills plus auth/history/sqlite/session state local to this machine.
if [[ -d "$DOTFILES/.codex/skills" ]]; then
  mkdir -p "$HOME/.codex/skills"
  for item in "$DOTFILES"/.codex/skills/*; do
    [[ -e "$item" || -L "$item" ]] || continue
    local_name="$(basename "$item")"
    link_tree "$item" "$HOME/.codex/skills/$local_name"
  done
fi

if [[ -d "$DOTFILES/.codex/memories" ]]; then
  link_tree "$DOTFILES/.codex/memories" "$HOME/.codex/memories"
fi

# ~/.*rc, ~/.gitconfig, etc.
for item in "$DOTFILES"/.[!.]*; do
  local_name="$(basename "$item")"
  [[ "$local_name" == ".git" || "$local_name" == ".config" || "$local_name" == ".claude" || "$local_name" == ".codex" || "$local_name" == ".vscode" ]] && continue
  if [[ -d "$item" && ! -L "$item" ]]; then
    link_tree "$item" "$HOME/$local_name"
  else
    link "$item" "$HOME/$local_name"
  fi
done

# ~/Library/KeyBindings
link_tree "$DOTFILES/.config/KeyBindings" "$HOME/Library/KeyBindings"

# Warn about macOS Application Support configs shadowing ~/.config/
for app_dir in "$HOME/Library/Application Support"/*/; do
  [[ -d "$app_dir" ]] || continue
  app_basename="$(basename "$app_dir")"
  app_tail="${app_basename##*.}"
  for cfg_dir in "$DOTFILES"/.config/*/; do
    cfg_name="$(basename "$cfg_dir")"
    app_tail_lower="$(echo "$app_tail" | tr '[:upper:]' '[:lower:]')"
    app_base_lower="$(echo "$app_basename" | tr '[:upper:]' '[:lower:]')"
    cfg_lower="$(echo "$cfg_name" | tr '[:upper:]' '[:lower:]')"
    if [[ "$app_tail_lower" == "$cfg_lower" || "$app_base_lower" == "$cfg_lower" ]]; then
      for cfg_file in "$cfg_dir"/*; do
        [[ -f "$cfg_file" ]] || continue
        shadow="$app_dir/$(basename "$cfg_file")"
        [[ -f "$shadow" && ! -L "$shadow" ]] && echo "⚠️  $shadow shadows ~/.config/$cfg_name/ — delete it to use the dotfiles version"
      done
      break
    fi
  done
done

# scripts/bin -> ~/.local/bin
mkdir -p "$HOME/.local/bin"
for script in "$DOTFILES"/scripts/bin/*.sh; do
  link "$script" "$HOME/.local/bin/$(basename "$script" .sh)"
done

# This script itself -> ~/.local/bin
link "$DOTFILES/scripts/sync/sync-dotfiles.sh" "$HOME/.local/bin/sync-dotfiles"

# VS Code — bidirectional sync with secrets splitting
# The live settings file is NOT symlinked (secrets would leak to repo).
# Instead, on each sync:
#   1. Extract secret keys from live file → local secrets store
#   2. Copy live file (sans secrets) back to repo
#   3. If no live file exists, build one from repo + secrets
vscode_dir="$HOME/Library/Application Support/Code/User"
mkdir -p "$vscode_dir"

# Symlink files that don't contain secrets
for f in keybindings.json extensions.json; do
  [[ -f "$DOTFILES/.vscode/$f" ]] && link "$DOTFILES/.vscode/$f" "$vscode_dir/$f"
done

# settings.json — secret-aware bidirectional sync
local_settings="$vscode_dir/settings.json"
repo_settings="$DOTFILES/.vscode/settings.json"
secrets_file="$DOTFILES/.vscode/settings.secrets.json"
if [[ -L "$local_settings" ]]; then
  rm "$local_settings"
fi
if [[ -f "$repo_settings" ]]; then
  "$DOTFILES/scripts/sync/sync-vscode-settings.py" "$repo_settings" "$local_settings" "$secrets_file"
fi

# Graphite — bidirectional preferences sync (authToken stays local)
gt_prefs="$DOTFILES/.config/graphite/preferences.json"
gt_config="$HOME/.config/graphite/user_config"

if [[ -f "$gt_prefs" && -f "$gt_config" ]]; then
  "$DOTFILES/scripts/sync/sync-graphite.py" "$gt_prefs" "$gt_config"
elif [[ -f "$gt_prefs" && ! -f "$gt_config" ]]; then
  # Fresh machine, no config yet — just copy preferences (user needs to run gt auth first)
  mkdir -p "$(dirname "$gt_config")"
  cp "$gt_prefs" "$gt_config"
  echo "ℹ️  Copied Graphite preferences. Run 'gt auth' to add your auth token."
fi
# Upgrade global uv tools (sourcery, etc.)
uv tool upgrade --all >/dev/null 2>&1 || true

# macOS defaults — read current values and update snapshot
if [[ -z "${SKIP_DEFAULTS_SYNC:-}" ]]; then
  "$DOTFILES/scripts/sync/sync-macos-defaults.py"
fi
