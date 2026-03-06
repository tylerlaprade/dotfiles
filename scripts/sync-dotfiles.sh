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
  [[ "$local_name" == ".git" || "$local_name" == ".config" || "$local_name" == ".claude" ]] && continue
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
