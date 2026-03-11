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
mkdir -p ~/.claude
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
    if [[ -f "$local_mkts" ]]; then
      # Sync marketplaces bidirectionally using manifest to detect deletions
      # Output format: "install <repo>" for new, "remove <name>" for deleted
      actions=$(python3 -c "
import json, sys, os
repo_path, local_path, manifest_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(repo_path) as f: repo = json.load(f)
with open(local_path) as f: local = json.load(f)
synced = set()
if os.path.exists(manifest_path):
    synced = set(open(manifest_path).read().split())
for k, v in list(repo.items()):
    if k not in local:
        if k in synced:
            del repo[k]  # was synced before, now deleted locally — remove from repo
            print(f'remove {k}')
        else:
            print(f'install {v[\"source\"][\"repo\"]}')  # new from another machine
for k, v in local.items():
    if k not in repo:
        repo[k] = {'source': v['source']}
with open(repo_path, 'w') as f:
    json.dump(repo, f, indent=2)
    f.write('\n')
" "$repo_mkts" "$local_mkts" "$manifest")
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
    else
      # No local file yet — install all repo marketplaces from scratch
      python3 -c "
import json, sys
with open(sys.argv[1]) as f: data = json.load(f)
for v in data.values():
    print(v['source']['repo'])
" "$repo_mkts" | while IFS= read -r repo_url; do
        claude plugin marketplace add "$repo_url" 2>/dev/null || true
      done
    fi
    # Update manifest with current local state
    if [[ -f "$local_mkts" ]]; then
      python3 -c "
import json, sys
with open(sys.argv[1]) as f: data = json.load(f)
print('\n'.join(data.keys()))
" "$local_mkts" > "$manifest"
    fi
  elif [[ -d "$item" && ! -L "$item" ]]; then
    link_tree "$item" "$HOME/.claude/$local_name"
  else
    link "$item" "$HOME/.claude/$local_name"
  fi
done

# ~/.*rc, ~/.gitconfig, etc.
for item in "$DOTFILES"/.[!.]*; do
  local_name="$(basename "$item")"
  [[ "$local_name" == ".git" || "$local_name" == ".config" || "$local_name" == ".claude" || "$local_name" == ".vscode" ]] && continue
  if [[ -d "$item" && ! -L "$item" ]]; then
    link_tree "$item" "$HOME/$local_name"
  else
    link "$item" "$HOME/$local_name"
  fi
done

# scripts -> ~/.local/bin
mkdir -p ~/.local/bin
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
  python3 -c "
import json, sys

prefs_path, config_path = sys.argv[1], sys.argv[2]
with open(prefs_path) as f: prefs = json.load(f)
with open(config_path) as f: config = json.load(f)

# Sync FROM local: extract non-secret preferences from config back to repo
local_prefs = {k: v for k, v in config.items()
               if k not in ('authToken', 'alternativeProfiles')}

# Sync TO local: merge repo preferences into config, preserving auth
for k, v in prefs.items():
    if k not in config:
        config[k] = v

# If local has changed preferences, update repo
if local_prefs != prefs:
    with open(prefs_path, 'w') as f:
        json.dump(local_prefs, f, indent=2)
        f.write('\n')

# Write merged config back
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$gt_prefs" "$gt_config"
elif [[ -f "$gt_prefs" && ! -f "$gt_config" ]]; then
  # Fresh machine, no config yet — just copy preferences (user needs to run gt auth first)
  mkdir -p "$(dirname "$gt_config")"
  cp "$gt_prefs" "$gt_config"
  echo "ℹ️  Copied Graphite preferences. Run 'gt auth' to add your auth token."
fi
