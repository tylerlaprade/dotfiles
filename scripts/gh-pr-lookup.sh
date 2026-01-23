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

# Occasionally wipe cache (but preserve current branch entry)
if [[ $((RANDOM % 50000000)) -eq 0 ]]; then
  current_entry=$(grep -m1 "^$key	" "$pr_map" 2>/dev/null)
  rm -f "$pr_map"
  [[ -n "$current_entry" ]] && echo "$current_entry" > "$pr_map"
fi

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
