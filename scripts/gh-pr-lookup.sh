#!/bin/bash
# Lookup PR number + title for a branch, using persistent cache
# Usage: gh-pr-lookup <repo> <branch> [--async]
# Outputs: number\ttitle (tab-separated, cached indefinitely)

repo="$1"
branch="$2"
[[ -z "$repo" || -z "$branch" ]] && exit 0

pr_map="$HOME/.cache/gh-pr-map"
mkdir -p "$(dirname "$pr_map")"
key="$repo:$branch"

# Randomly wipe cache (~1 in 32768 calls)
[[ $RANDOM -eq 0 ]] && rm -f "$pr_map"

cached=$(grep -m1 "^$key	" "$pr_map" 2>/dev/null | cut -f2-)
if [[ -n "$cached" ]]; then
  echo "$cached"
elif [[ "${3:-}" == "--async" ]]; then
  # Query in background, don't output anything this time
  (
    result=$(gh pr view --json number,title -q '"\(.number)\t\(.title)"' 2>/dev/null)
    if [[ -n "$result" ]]; then
      ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$result" >> "$pr_map"
    fi
  ) &
else
  # Query synchronously
  result=$(gh pr view --json number,title -q '"\(.number)\t\(.title)"' 2>/dev/null)
  if [[ -n "$result" ]]; then
    ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$result" >> "$pr_map"
    echo "$result"
  fi
fi
