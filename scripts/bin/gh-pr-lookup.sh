#!/bin/bash
# Look up PR number + title for a branch, using persistent cache with TTL.
# Usage: gh-pr-lookup <repo> <branch> [--async]
# Outputs: number\ttitle  (or nothing if no PR / cache miss in --async mode)
# Cache format: <key>\t<result-or-__NONE__>\t<unix-ts>
# TTL: real PRs 300s; "no PR" sentinel 30s, so a freshly-pushed PR appears
# in the tab title within ~30s. Stale entries served immediately while a bg
# refresh runs.

repo="$1"
branch="$2"
async=0
[[ "${3:-}" == "--async" ]] && async=1
[[ -z "$repo" || -z "$branch" ]] && exit 0

pr_map="$HOME/.cache/gh-pr-map"
mkdir -p "$(dirname "$pr_map")"
key="$repo:$branch"
now=$(date +%s)

# Spawn a detached refresh. CRITICAL: redirect fds before the fork so the
# caller's $(...) command-substitution doesn't block waiting on the inherited
# stdout/stderr of the bg subshell.
_spawn_refresh() {
  (
    exec >/dev/null 2>&1 </dev/null
    # `gh pr list` distinguishes "no PR" (exit 0, []) from errors (exit !=0),
    # unlike `gh pr view` which exits 1 in both cases.
    result=$(gh pr list --head "$branch" --limit 1 --json number,title \
              --jq '.[]? | "\(.number)\t\(.title)"' 2>/dev/null)
    rc=$?
    [[ $rc -ne 0 ]] && exit 0   # transient error → leave cache untouched
    new_line="$key	${result:-__NONE__}	$now"
    lock="$pr_map.lock"
    # mkdir is atomic; use as a lockdir to serialize cache rewrites.
    if mkdir "$lock" 2>/dev/null; then
      tmp="$pr_map.tmp.$$"
      grep -v "^$key	" "$pr_map" 2>/dev/null >"$tmp"
      echo "$new_line" >>"$tmp"
      mv "$tmp" "$pr_map"
      rmdir "$lock"
    fi
  ) &
  disown 2>/dev/null
}

entry=$(grep -m1 "^$key	" "$pr_map" 2>/dev/null)

if [[ -n "$entry" ]]; then
  cached=$(printf '%s' "$entry" | cut -f2)
  ts=$(printf '%s' "$entry" | cut -f3)
  ttl=300
  [[ "$cached" == "__NONE__" ]] && ttl=30
  age=$(( now - ${ts:-0} ))

  [[ "$cached" != "__NONE__" ]] && echo "$cached"
  (( age > ttl )) && _spawn_refresh
  exit 0
fi

# Cache miss
if (( async )); then
  _spawn_refresh
else
  result=$(gh pr list --head "$branch" --limit 1 --json number,title \
            --jq '.[]? | "\(.number)\t\(.title)"' 2>/dev/null)
  if [[ $? -eq 0 ]]; then
    echo "$key	${result:-__NONE__}	$now" >>"$pr_map"
    [[ -n "$result" ]] && echo "$result"
  fi
fi
