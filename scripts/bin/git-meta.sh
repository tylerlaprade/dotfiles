#!/bin/bash
# Cached git metadata for current dir, shared across processes.
# Output (one line, tab-separated): <repo>\t<repo_full>\t<branch>\t<common_dir>
#   repo:       basename of toplevel (e.g. "dotfiles")
#   repo_full:  GitHub-style "owner/name" from origin remote (may be empty)
#   branch:     current branch (or "HEAD" if detached)
#   common_dir: absolute git common dir, shared by all worktrees
# Returns nonzero (no output) if not inside a git repo.
#
# Cache: ~/.cache/git-meta, keyed by realpath PWD.
# Schema: <pwd>\t<repo>\t<repo_full>\t<branch>\t<gitdir>\t<head_mtime>\t<config_mtime>\t<common_dir>
# Invalidated when .git/HEAD or .git/config mtime changes (covers branch
# switches and remote URL changes). Pruned to last 50 distinct PWDs.

set -u
cache="$HOME/.cache/git-meta"
pwd_real=$(pwd -P)

_emit() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4"; }

# Cache lookup
if [[ -f "$cache" ]]; then
  line=$(grep -m1 "^${pwd_real}	" "$cache" 2>/dev/null) || true
  if [[ -n "$line" ]]; then
    IFS=$'\t' read -r _ repo repo_full branch gitdir head_mtime config_mtime common_dir <<<"$line"
    { read -r cur_head; read -r cur_cfg; } < <(stat -f %m "$gitdir/HEAD" "$gitdir/config" 2>/dev/null)
    if [[ -n "$common_dir" && -n "$cur_head" && "$cur_head" == "$head_mtime" && "$cur_cfg" == "$config_mtime" ]]; then
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
repo_full=$(git remote get-url origin 2>/dev/null | sed -E 's#(git@|https://)github\.com[:/]##; s#\.git$##')
head_mtime=$(stat -f %m "$gitdir/HEAD" 2>/dev/null || echo)
config_mtime=$(stat -f %m "$gitdir/config" 2>/dev/null || echo)

# Atomic cache rewrite, pruned to last 50 entries (LRU-ish — most recently
# refreshed entries float to the bottom of the file).
new_line="${pwd_real}	${repo}	${repo_full}	${branch}	${gitdir}	${head_mtime}	${config_mtime}	${common_dir}"
lock="$cache.lock"
mkdir -p "${cache%/*}"
if mkdir "$lock" 2>/dev/null; then
  tmp="$cache.tmp.$$"
  {
    grep -v "^${pwd_real}	" "$cache" 2>/dev/null || true
    echo "$new_line"
  } | tail -n 50 > "$tmp"
  mv "$tmp" "$cache"
  rmdir "$lock"
fi

_emit "$repo" "$repo_full" "$branch" "$common_dir"
