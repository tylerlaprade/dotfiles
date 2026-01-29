#!/bin/bash
# Lookup PR number and state for a branch, using persistent cache
# Usage: gh-pr-lookup <repo> <branch> [--async]
# Outputs: number:state (e.g., "123:approved", "456:merged")
# States: merged, draft, approved, changes_requested, pending

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

# Helper to determine state from gh pr view JSON
get_pr_state() {
  local json="$1"
  local state isDraft reviewDecision
  state=$(echo "$json" | jq -r '.state')
  isDraft=$(echo "$json" | jq -r '.isDraft')
  reviewDecision=$(echo "$json" | jq -r '.reviewDecision // empty')

  if [[ "$state" == "MERGED" ]]; then
    echo "merged"
  elif [[ "$isDraft" == "true" ]]; then
    echo "draft"
  elif [[ "$reviewDecision" == "APPROVED" ]]; then
    echo "approved"
  elif [[ "$reviewDecision" == "CHANGES_REQUESTED" ]]; then
    echo "changes_requested"
  else
    echo "pending"
  fi
}

cached=$(grep -m1 "^$key	" "$pr_map" 2>/dev/null | cut -f2)
if [[ -n "$cached" ]]; then
  echo "$cached"
elif [[ "${3:-}" == "--async" ]]; then
  # Query in background, don't output anything this time
  (
    json=$(gh pr view --json number,state,isDraft,reviewDecision 2>/dev/null)
    if [[ -n "$json" ]]; then
      num=$(echo "$json" | jq -r '.number')
      state=$(get_pr_state "$json")
      [[ -n "$num" ]] && ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$num:$state" >> "$pr_map"
    fi
  ) &
else
  # Query synchronously
  json=$(gh pr view --json number,state,isDraft,reviewDecision 2>/dev/null)
  if [[ -n "$json" ]]; then
    num=$(echo "$json" | jq -r '.number')
    state=$(get_pr_state "$json")
    if [[ -n "$num" ]]; then
      ! grep -q "^$key	" "$pr_map" 2>/dev/null && echo "$key	$num:$state" >> "$pr_map"
      echo "$num:$state"
    fi
  fi
fi
