#!/bin/bash
# Lightweight symlink sync — safe to run repeatedly.
# Run by the com.tylerlaprade.sync-dotfiles LaunchAgent (at login and daily).

_source="${BASH_SOURCE[0]}"
while [[ -L "$_source" ]]; do
  _source="$(readlink "$_source")"
done
DOTFILES="$(cd "$(dirname "$_source")/../.." && pwd)"
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Paths handled by dedicated sync scripts below. link_tree skips these so
# it doesn't symlink-then-clobber (which accumulated `.pre-dotfiles-*`
# backups on every invocation). Populated by dedicated_sync().
DEDICATED_SYNC_PATHS=()

dedicated_sync() {
  local dst="$1"
  shift
  DEDICATED_SYNC_PATHS+=("$dst")
  if [[ -L "$dst" ]]; then
    rm "$dst"
  fi
  "$@" "$dst"
}

link() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    ln -snf "$src" "$dst"
  elif [[ -e "$dst" ]]; then
    local backup
    backup="${dst}.pre-dotfiles-$(date +%Y%m%d%H%M%S)"
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
    local name
    name="$(basename "$item")"
    local dst="$dst_dir/$name"
    local skip=0
    for skip_path in "${DEDICATED_SYNC_PATHS[@]}"; do
      [[ "$dst" == "$skip_path" ]] && skip=1 && break
    done
    [[ $skip -eq 1 ]] && continue
    if [[ -d "$item" && ! -L "$item" ]]; then
      link_tree "$item" "$dst"
    else
      link "$item" "$dst"
    fi
  done
}

# Helix languages.toml — secret-aware bidirectional sync (has Sourcery token).
# Must run before link_tree so the path gets registered in DEDICATED_SYNC_PATHS.
dedicated_sync "$HOME/.config/helix/languages.toml" \
  "$DOTFILES/scripts/sync/sync-helix-languages.py" \
  "$DOTFILES/.config/helix/languages.toml"

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

# Shared global agent prefs (all hosts). Claude Code has no user-level
# AGENTS.md, so ~/.claude/CLAUDE.md links straight to the shared file. Keep
# it out of this repo's .claude/: a .claude/CLAUDE.md in the working tree
# counts as project instructions and would suppress this repo's AGENTS.md.
mkdir -p "$HOME/.agents"
link "$DOTFILES/.agents/AGENTS.md" "$HOME/.agents/AGENTS.md"
link "$DOTFILES/.agents/AGENTS.md" "$HOME/.claude/CLAUDE.md"

# Codex global instructions.
mkdir -p "$HOME/.codex"
link "$DOTFILES/.agents/AGENTS.md" "$HOME/.codex/AGENTS.md"

# Codex skills — symlink each from .codex/skills/ (skip .system, owned by Codex).
mkdir -p "$HOME/.codex/skills"
for item in "$DOTFILES"/.codex/skills/*; do
  [[ -e "$item" || -L "$item" ]] || continue
  link "$item" "$HOME/.codex/skills/$(basename "$item")"
done

# Antigravity CLI (agy) global context. It took over the old
# Gemini CLI home at ~/.gemini and still reads GEMINI.md there.
mkdir -p "$HOME/.gemini"
link "$DOTFILES/.agents/AGENTS.md" "$HOME/.gemini/GEMINI.md"
link "$DOTFILES/.gemini/settings.json" "$HOME/.gemini/settings.json"

# Grok global rules. Pair with [compat.claude] agents = false in
# ~/.grok/config.toml so Grok does not load the same file twice through
# ~/.claude/CLAUDE.md.
mkdir -p "$HOME/.grok/rules"
link "$DOTFILES/.agents/AGENTS.md" "$HOME/.grok/rules/agents.md"
link "$DOTFILES/.grok/config.toml" "$HOME/.grok/config.toml"

# Agent Skills (agentskills.io standard) — symlink each skill folder as a whole
# so the dir stays a single link instead of a per-file mirror.
mkdir -p "$HOME/.agents/skills"
for item in "$DOTFILES"/.agents/skills/*; do
  [[ -e "$item" || -L "$item" ]] || continue
  link "$item" "$HOME/.agents/skills/$(basename "$item")"
done

# ~/.gitconfig stays a real file so machine-local keys (CodeRabbit's machine
# id) are not written into the shared repo. Git reads the shared settings
# through an include.
ensure_gitconfig() {
  local shared="$DOTFILES/.gitconfig"
  local dest="$HOME/.gitconfig"
  local machine_id=""
  if [[ -e "$dest" || -L "$dest" ]]; then
    machine_id=$(git config --file "$dest" --get coderabbit.machineId 2>/dev/null || true)
  fi
  if [[ -L "$dest" ]]; then
    rm "$dest"
  fi
  if [[ ! -f "$dest" ]]; then
    printf '[include]\n\tpath = %s\n' "$shared" > "$dest"
  else
    local include_paths
    include_paths=$(git config --file "$dest" --get-all include.path 2>/dev/null || true)
    if ! grep -Fxq "$shared" <<< "$include_paths"; then
      git config --file "$dest" --add include.path "$shared"
    fi
  fi
  if [[ -n "$machine_id" ]]; then
    local current=""
    current=$(git config --file "$dest" --get coderabbit.machineId 2>/dev/null || true)
    if [[ "$current" != "$machine_id" ]]; then
      git config --file "$dest" coderabbit.machineId "$machine_id"
    fi
  fi
}

# ~/.*rc, ~/.gitconfig, etc.
for item in "$DOTFILES"/.[!.]*; do
  local_name="$(basename "$item")"
  [[ "$local_name" == ".git" || "$local_name" == ".config" || "$local_name" == ".claude" || "$local_name" == ".codex" || "$local_name" == ".agents" || "$local_name" == ".vscode" ]] && continue
  if [[ "$local_name" == ".gitconfig" ]]; then
    ensure_gitconfig
    continue
  fi
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

# LaunchAgents
mkdir -p "$HOME/Library/LaunchAgents"
for plist in "$DOTFILES"/LaunchAgents/*.plist; do
  [[ -f "$plist" ]] || continue
  link "$plist" "$HOME/Library/LaunchAgents/$(basename "$plist")"
done

# scripts/bin -> ~/.local/bin
mkdir -p "$HOME/.local/bin"
for script in "$DOTFILES"/scripts/bin/*.sh; do
  link "$script" "$HOME/.local/bin/$(basename "$script" .sh)"
done

# This script itself -> ~/.local/bin
link "$DOTFILES/scripts/sync/sync-dotfiles.sh" "$HOME/.local/bin/sync-dotfiles"

# Make the personal lint policy transparent to cargo, editors, hooks, and agents.
# The wrapper resolves the real toolchain binary through rustup, so this link
# replaces only rustup's cargo-clippy proxy, not Clippy itself.
link "$DOTFILES/scripts/cargo-clippy.py" "$HOME/.cargo/bin/cargo-clippy"

# Links whose repo file was removed or renamed
find "$HOME" "$HOME/Library/LaunchAgents" "$HOME/Library/KeyBindings" -maxdepth 5 \
  \( -path "$HOME/Library" -o -path "$HOME/Code" -o -path "$HOME/.Trash" -o -path "$HOME/.cache" -o -path "$HOME/.rustup" -o -path "$HOME/.cargo/registry" \) -prune \
  -o -type l -lname "$DOTFILES/*" ! -exec test -e {} \; -exec rm -v {} +

# Graphite — bidirectional preferences sync (authToken stays local).
# A missing base adopts the repo file inside the script.
gt_prefs="$DOTFILES/.config/graphite/preferences.json"
gt_config="$HOME/.config/graphite/user_config"

if [[ -f "$gt_prefs" ]]; then
  "$DOTFILES/scripts/sync/sync-graphite.py" "$gt_prefs" "$gt_config"
fi
# Tool upgrades — at most once a week, even when login-triggered runs stack up.
upgrade_stamp="$HOME/.cache/sync-dotfiles-upgrade-stamp"
recent_upgrade_stamp=$(find "$upgrade_stamp" -mtime -7 2>/dev/null)
if [[ -z "$recent_upgrade_stamp" ]]; then
  mkdir -p "$HOME/.cache" && touch "$upgrade_stamp"

  uv tool upgrade --all >/dev/null 2>&1 || true

  if command -v cargo >/dev/null 2>&1; then
    cargo install cargo-update >/dev/null 2>&1 || true
    cargo install-update -a >/dev/null 2>&1 || true
  fi

  if command -v bun >/dev/null 2>&1; then
    bun upgrade >/dev/null 2>&1 || true
  fi
fi

# macOS defaults — read current values and update snapshot
if [[ -z "${SKIP_DEFAULTS_SYNC:-}" ]]; then
  "$DOTFILES/scripts/sync/sync-macos-defaults.py"
  # Browser-wide Chromium settings (Memory Saver etc.) live in Local State,
  # which neither Brave Sync nor the defaults snapshot covers.
  "$DOTFILES/scripts/sync/sync-browser-local-state.py"
fi
