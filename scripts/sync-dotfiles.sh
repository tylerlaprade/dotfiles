#!/bin/bash
# Lightweight symlink sync — safe to run repeatedly.
# Called from Claude Code SessionStart hook to keep links current.

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

link() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    ln -snf "$src" "$dst"
  elif [[ -e "$dst" ]]; then
    echo "⚠️  Skipped $dst (file exists, not a symlink)"
    return
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

# ~/.claude/* (skip machine-local files)
mkdir -p "$HOME/.claude"
for item in "$DOTFILES"/.claude/*; do
  local_name="$(basename "$item")"
  [[ "$local_name" == "settings.local.json" ]] && continue
  if [[ "$local_name" == "plugins" ]]; then
    # Merge marketplace sources from repo into local file (don't symlink —
    # the plugins system adds machine-specific fields we don't want in the repo).
    mkdir -p "$HOME/.claude/plugins"
    local_mkts="$HOME/.claude/plugins/known_marketplaces.json"
    repo_mkts="$DOTFILES/.claude/plugins/known_marketplaces.json"
    if [[ -L "$local_mkts" ]]; then
      rm "$local_mkts"
    fi
    manifest="$HOME/.claude/plugins/.synced-marketplaces"
    actions=$("$DOTFILES/scripts/sync-claude-plugins.py" "$repo_mkts" "$local_mkts" "$manifest")
    if [[ -n "$actions" ]]; then
      while IFS= read -r action; do
        case "$action" in
          install\ *)
            claude plugin marketplace add "${action#install }" 2>/dev/null || true ;;
          remove\ *)
            claude plugin marketplace remove "${action#remove }" 2>/dev/null || true ;;
        esac
      done <<< "$actions"
    fi
  elif [[ -d "$item" && ! -L "$item" ]]; then
    link_tree "$item" "$HOME/.claude/$local_name"
  else
    link "$item" "$HOME/.claude/$local_name"
  fi
done

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

# scripts -> ~/.local/bin
mkdir -p "$HOME/.local/bin"
for script in "$DOTFILES"/scripts/*.sh; do
  link "$script" "$HOME/.local/bin/$(basename "$script" .sh)"
done

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
# Add secret key patterns here (grep -E pattern matching JSON keys)
secret_keys_pattern='"sourcery\.token"\s*:'

if [[ -f "$local_settings" && ! -L "$local_settings" ]]; then
  # Live file exists: extract secrets, sync non-secrets back to repo
  grep -E "$secret_keys_pattern" "$local_settings" | sed 's/^[[:space:]]*//' > "$secrets_file.tmp" 2>/dev/null
  if [[ -s "$secrets_file.tmp" ]]; then
    mv "$secrets_file.tmp" "$secrets_file"
  else
    rm -f "$secrets_file.tmp"
  fi
  # Copy live settings (minus secret lines) to repo
  grep -vE "$secret_keys_pattern" "$local_settings" > "$repo_settings.tmp"
  mv "$repo_settings.tmp" "$repo_settings"
elif [[ -f "$repo_settings" ]]; then
  # No live file (fresh machine): build from repo + secrets
  if [[ -f "$secrets_file" ]]; then
    # Insert secret lines before the final closing brace
    sed '$d' "$repo_settings" > "$local_settings"
    sed -i '' 's/[[:space:]]*$//' "$local_settings"
    while IFS= read -r line; do
      [[ -n "$line" ]] && printf '\t%s\n' "$line" >> "$local_settings"
    done < "$secrets_file"
    echo '}' >> "$local_settings"
  else
    cp "$repo_settings" "$local_settings"
  fi
fi
# Remove any leftover symlink from old sync
[[ -L "$local_settings" ]] && rm "$local_settings" && cp "$repo_settings" "$local_settings"

# Graphite — bidirectional preferences sync (authToken stays local)
gt_prefs="$DOTFILES/.config/graphite/preferences.json"
gt_config="$HOME/.config/graphite/user_config"

if [[ -f "$gt_prefs" && -f "$gt_config" ]]; then
  "$DOTFILES/scripts/sync-graphite.py" "$gt_prefs" "$gt_config"
elif [[ -f "$gt_prefs" && ! -f "$gt_config" ]]; then
  # Fresh machine, no config yet — just copy preferences (user needs to run gt auth first)
  mkdir -p "$(dirname "$gt_config")"
  cp "$gt_prefs" "$gt_config"
  echo "ℹ️  Copied Graphite preferences. Run 'gt auth' to add your auth token."
fi

# macOS defaults — read current values and update snapshot
if [[ -z "${SKIP_DEFAULTS_SYNC:-}" ]]; then
  "$DOTFILES/scripts/sync-macos-defaults.py"
fi
