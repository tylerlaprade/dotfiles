#!/bin/bash
# Lookup PR state/CI/mergeable for a branch
# Usage: gh-pr-status <repo> <branch> [--async]
# Outputs: state:ci:mergeable (e.g., "approved:pass:ok", "pending:fail:conflict")

repo="$1"
branch="$2"
[[ -z "$repo" || -z "$branch" ]] && exit 0

cache_file="$HOME/.cache/gh-pr-status"
mkdir -p "$(dirname "$cache_file")"
key="$repo:$branch"

# Return cached value if exists
cached=$(grep -m1 "^$key	" "$cache_file" 2>/dev/null | cut -f2)
[[ -n "$cached" ]] && echo "$cached"

# Helpers to parse gh output
get_state() {
  local json="$1"
  local state isDraft reviewDecision
  state=$(echo "$json" | jq -r '.state')
  isDraft=$(echo "$json" | jq -r '.isDraft')
  reviewDecision=$(echo "$json" | jq -r '.reviewDecision // empty')

  if [[ "$state" == "MERGED" ]]; then echo "merged"
  elif [[ "$isDraft" == "true" ]]; then echo "draft"
  elif [[ "$reviewDecision" == "APPROVED" ]]; then echo "approved"
  elif [[ "$reviewDecision" == "CHANGES_REQUESTED" ]]; then echo "changes_requested"
  else echo "pending"
  fi
}

get_ci() {
  local json="$1"
  if echo "$json" | jq -e '.statusCheckRollup[]? | select(.conclusion == "FAILURE")' &>/dev/null; then
    echo "fail"
  else
    echo "pass"
  fi
}

get_mergeable() {
  local json="$1"
  if [[ $(echo "$json" | jq -r '.mergeable') == "CONFLICTING" ]]; then
    echo "conflict"
  else
    echo "ok"
  fi
}

update_cache() {
  local value="$1"
  local temp_cache
  temp_cache=$(mktemp)
  grep -v "^$key	" "$cache_file" > "$temp_cache" 2>/dev/null || true
  printf '%s\t%s\n' "$key" "$value" >> "$temp_cache"
  mv "$temp_cache" "$cache_file"
}

fetch_and_cache() {
  json=$(gh pr view --json state,isDraft,reviewDecision,mergeable,statusCheckRollup 2>/dev/null)
  if [[ -n "$json" ]]; then
    update_cache "$(get_state "$json"):$(get_ci "$json"):$(get_mergeable "$json")"
  fi
}

if [[ "${3:-}" == "--async" ]]; then
  fetch_and_cache &
else
  fetch_and_cache
fi
