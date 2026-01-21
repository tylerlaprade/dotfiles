#!/bin/bash
# Lookup PR number for a branch, using persistent cache
# Usage: gh-pr-lookup <repo> <branch>
# Outputs PR number if found, nothing otherwise

repo="$1"
branch="$2"
[[ -z "$repo" || -z "$branch" ]] && exit 0

pr_map="$HOME/.cache/gh-pr-map"
mkdir -p "$(dirname "$pr_map")"
key="$repo:$branch"

# Occasionally wipe cache
[[ $((RANDOM % 5000)) -eq 0 ]] && rm -f "$pr_map"

pr=$(grep -m1 "^$key	" "$pr_map" 2>/dev/null | cut -f2)
if [[ -n "$pr" ]]; then
  echo "$pr"
elif [[ "${3:-}" == "--async" ]]; then
  # Query in background, don't output anything this time
  (
    num=$(gh pr view --json number -q .number 2>/dev/null)
    [[ -n "$num" ]] && ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$num" >> "$pr_map"
  ) &
else
  # Query synchronously
  num=$(gh pr view --json number -q .number 2>/dev/null)
  if [[ -n "$num" ]]; then
    ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$num" >> "$pr_map"
    echo "$num"
  fi
fi
