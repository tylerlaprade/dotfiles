#!/bin/bash
# Cached git metadata for current dir, shared across processes.
# Output (one line, tab-separated): <repo>\t<repo_full>\t<branch>
#   repo:       basename of toplevel (e.g. "dotfiles")
#   repo_full:  GitHub-style "owner/name" from origin remote (may be empty)
#   branch:     current branch (or "HEAD" if detached)
# Returns nonzero (no output) if not inside a git repo.
#
# Cache: ~/.cache/git-meta, keyed by realpath PWD.
# Schema: <pwd>\t<repo>\t<repo_full>\t<branch>\t<gitdir>\t<head_mtime>\t<config_mtime>
# Invalidated when .git/HEAD or .git/config mtime changes (covers branch
# switches and remote URL changes). Pruned to last 50 distinct PWDs.

set -u
cache="$HOME/.cache/git-meta"
mkdir -p "$(dirname "$cache")"
pwd_real=$(pwd -P)

_emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

# Cache lookup
if [[ -f "$cache" ]]; then
  line=$(grep -m1 "^${pwd_real}	" "$cache" 2>/dev/null) || true
  if [[ -n "$line" ]]; then
    IFS=$'\t' read -r _ repo repo_full branch gitdir head_mtime config_mtime <<<"$line"
    cur_head=$(stat -f %m "$gitdir/HEAD" 2>/dev/null || echo)
    cur_cfg=$(stat -f %m "$gitdir/config" 2>/dev/null || echo)
    if [[ -n "$cur_head" && "$cur_head" == "$head_mtime" && "$cur_cfg" == "$config_mtime" ]]; then
      _emit "$repo" "$repo_full" "$branch"
      exit 0
    fi
  fi
fi

# Cache miss / stale → fork git once for all three values
out=$(git rev-parse --show-toplevel --abbrev-ref HEAD --git-dir 2>/dev/null) || exit 1
toplevel="${out%%$'\n'*}"
rest="${out#*$'\n'}"
branch="${rest%%$'\n'*}"
gitdir="${rest##*$'\n'}"
[[ "$gitdir" != /* ]] && gitdir="$pwd_real/$gitdir"
repo=$(basename "$toplevel")
repo_full=$(git remote get-url origin 2>/dev/null | sed -E 's#(git@|https://)github\.com[:/]##; s#\.git$##')
head_mtime=$(stat -f %m "$gitdir/HEAD" 2>/dev/null || echo)
config_mtime=$(stat -f %m "$gitdir/config" 2>/dev/null || echo)

# Atomic cache rewrite, pruned to last 50 entries (LRU-ish — most recently
# refreshed entries float to the bottom of the file).
new_line="${pwd_real}	${repo}	${repo_full}	${branch}	${gitdir}	${head_mtime}	${config_mtime}"
lock="$cache.lock"
if mkdir "$lock" 2>/dev/null; then
  tmp="$cache.tmp.$$"
  {
    grep -v "^${pwd_real}	" "$cache" 2>/dev/null || true
    echo "$new_line"
  } | tail -n 50 > "$tmp"
  mv "$tmp" "$cache"
  rmdir "$lock"
fi

_emit "$repo" "$repo_full" "$branch"
