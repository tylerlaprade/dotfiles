#!/bin/bash
# Cached git metadata for current dir, shared across processes.
# Output (one line, separated by the unit separator \037): <repo> <repo_full> <branch> <common_dir>
#   repo:       basename of toplevel (e.g. "dotfiles")
#   repo_full:  "owner/name" when origin is on GitHub, otherwise empty
#   branch:     current branch (or "HEAD" if detached)
#   common_dir: absolute git common dir, shared by all worktrees
# Returns nonzero (no output) if not inside a git repo.
#
# Cache: ~/.cache/git-meta, keyed by realpath PWD.
# Schema, same separator: <pwd> <repo> <repo_full> <branch> <gitdir> <head_mtime> <config_mtime> <common_dir>
# Invalidated when .git/HEAD or .git/config mtime changes (covers branch
# switches and remote URL changes). Pruned to last 50 distinct PWDs.

set -u
cache="$HOME/.cache/git-meta"
pwd_real=$(pwd -P)
# Not tab: read treats tab as whitespace and merges adjacent ones, so an empty
# repo_full would shift every later field.
sep=$'\037'

_emit() { printf '%s\n' "$1$sep$2$sep$3$sep$4"; }

# Cache lookup
if [[ -f "$cache" ]]; then
  line=$(grep -m1 "^${pwd_real}${sep}" "$cache" 2>/dev/null) || true
  if [[ -n "$line" ]]; then
    IFS=$sep read -r _ repo repo_full branch gitdir head_mtime config_mtime common_dir <<<"$line"
    mtimes=$(stat -f %m "$gitdir/HEAD" "$gitdir/config" 2>/dev/null) || true
    if [[ -n "$common_dir" && "$mtimes" == "$head_mtime"$'\n'"$config_mtime" ]]; then
      _emit "$repo" "$repo_full" "$branch" "$common_dir"
      exit 0
    fi
  fi
fi

# Cache miss / stale → fork git once for all four values
out=$(git rev-parse --show-toplevel --abbrev-ref HEAD --git-dir --git-common-dir 2>/dev/null) || exit 1
{ read -r toplevel; read -r branch; read -r gitdir; read -r common_dir; } <<<"$out"
[[ "$gitdir" != /* ]] && gitdir="$pwd_real/$gitdir"
[[ "$common_dir" != /* ]] && common_dir="$pwd_real/$common_dir"
repo=$(basename "$toplevel")
origin_url=$(git remote get-url origin 2>/dev/null) || true
repo_full=""
[[ "$origin_url" =~ ^(ssh://git@|git@|https://)github\.com[:/](.+)$ ]] && repo_full=${BASH_REMATCH[2]%.git}
head_mtime=$(stat -f %m "$gitdir/HEAD" 2>/dev/null || echo)
config_mtime=$(stat -f %m "$gitdir/config" 2>/dev/null || echo)

# Atomic cache rewrite, pruned to last 50 entries (LRU-ish — most recently
# refreshed entries float to the bottom of the file).
new_line="${pwd_real}${sep}${repo}${sep}${repo_full}${sep}${branch}${sep}${gitdir}${sep}${head_mtime}${sep}${config_mtime}${sep}${common_dir}"
lock="$cache.lock"
mkdir -p "${cache%/*}"

# mkdir is atomic. A refresh killed mid-flight leaves its lock behind, so a
# lock older than max_age is leftover, not in-flight.
claim_lock() {
  local lock=$1 max_age=$2 stamp now
  mkdir "$lock" 2>/dev/null && return 0
  stamp=$(stat -f %m "$lock" 2>/dev/null) || return 1
  now=$(date +%s)
  (( now - stamp > max_age )) || return 1
  rmdir "$lock" 2>/dev/null
  mkdir "$lock" 2>/dev/null
}

if claim_lock "$lock" 30; then
  tmp="$cache.tmp.$$"
  {
    grep -v "^${pwd_real}${sep}" "$cache" 2>/dev/null || true
    echo "$new_line"
  } | tail -n 50 > "$tmp"
  mv "$tmp" "$cache"
  rmdir "$lock"
fi

_emit "$repo" "$repo_full" "$branch" "$common_dir"
