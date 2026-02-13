#!/bin/bash
# Cached Graphite branch metadata lookup (no gt CLI).
# Usage: gt-status <repo> <branch> [--async]
# Output: total:depth:unsubmitted (e.g., "5:2:1"), or empty if no Graphite branches.
# Cache is keyed by repo:branch; async mode computes in background on miss.

repo="$1"
branch="$2"
[[ -z "$repo" || -z "$branch" ]] && exit 0

cache_file="$HOME/.cache/gt-status-map"
mkdir -p "$(dirname "$cache_file")"
key="$repo:$branch"

# Randomly wipe cache (~1 in 32768 calls)
[[ $RANDOM -eq 0 ]] && rm -f "$cache_file"

compute() {
  git rev-parse --git-dir &>/dev/null || return

  local refs
  refs=$(git for-each-ref --format='%(refname:strip=2) %(objectname)' refs/branch-metadata/ 2>/dev/null)
  [[ -z "$refs" ]] && return

  # Only include metadata for branches that still exist locally
  local local_branches
  local_branches=$(git for-each-ref --format='%(refname:strip=2)' refs/heads/ 2>/dev/null)

  # Build combined JSON array from refs with existing local branches
  local json_array="[" first=true
  while IFS=' ' read -r name sha; do
    echo "$local_branches" | grep -qxF "$name" || continue
    local blob
    blob=$(git cat-file -p "$sha" 2>/dev/null)
    [[ -z "$blob" ]] && continue
    $first || json_array+=","
    first=false
    json_array+="{\"_name\":\"$name\",\"_data\":$blob}"
  done <<< "$refs"
  json_array+="]"

  echo "$json_array" | jq -r --arg current "$branch" '
    map(select(._data.validationResult != "TRUNK")) as $features |
    ($features | length) as $total |
    if $total == 0 then "" else
    ($features | map(select(._data.lastSubmittedVersion == null)) | length) as $unsub |
    (map({(._name): (._data.parentBranchName // "")}) | add // {}) as $parents |
    (map({(._name): (._data.validationResult // "")}) | add // {}) as $vals |
    {d: 0, w: $current} |
    until(
      (.w == "") or ($vals[.w] == "TRUNK") or ($parents[.w] == null);
      if $parents[.w] == "" or $parents[.w] == null then .w = ""
      elif $vals[$parents[.w]] == "TRUNK" then .d += 1 | .w = ""
      else .d += 1 | .w = $parents[.w]
      end
    ) |
    "\($total):\(.d):\($unsub)"
    end
  '
}

# Only cache results with no unsubmitted branches (mirrors gh-pr-lookup: don't cache misses)
should_cache() {
  [[ -n "$1" && "${1##*:}" == "0" ]]
}

cached=$(grep -m1 "^$key	" "$cache_file" 2>/dev/null | cut -f2)
if [[ -n "$cached" ]]; then
  echo "$cached"
elif [[ "${3:-}" == "--async" ]]; then
  (
    result=$(compute)
    if [[ -n "$result" ]]; then
      should_cache "$result" && ! grep -q "^$key	" "$cache_file" 2>/dev/null && echo "$key	$result" >> "$cache_file"
    fi
  ) &
else
  result=$(compute)
  if [[ -n "$result" ]]; then
    should_cache "$result" && ! grep -q "^$key	" "$cache_file" 2>/dev/null && echo "$key	$result" >> "$cache_file"
    echo "$result"
  fi
fi
