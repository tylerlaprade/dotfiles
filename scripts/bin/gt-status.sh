#!/bin/bash
# Cached Graphite branch metadata lookup (no gt CLI).
# Usage: gt-status <repo> <branch> [--async]
# Output: total:depth:unsubmitted (e.g., "5:2:1"), or empty if no Graphite branches.
# Cache format: <repo:branch>\t<result>\t<unix-ts>, empty results included.
# Entries older than the TTL are recomputed; async mode serves the stale entry
# while a detached refresh runs.

repo="$1"
branch="$2"
[[ -z "$repo" || -z "$branch" ]] && exit 0

cache_file="$HOME/.cache/gt-status-cache"
key="$repo:$branch"
ttl=60
now=$(date +%s)

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

store() {
  local result=$1 tmp
  mkdir -p "${cache_file%/*}"
  tmp=$(mktemp "${cache_file}.XXXXXX")
  awk -F '\t' -v key="$key" '$1 != key' "$cache_file" 2>/dev/null > "$tmp"
  printf '%s\t%s\t%s\n' "$key" "$result" "$now" >> "$tmp"
  mv "$tmp" "$cache_file"
}

entry=$(awk -F '\t' -v key="$key" '$1 == key { print; exit }' "$cache_file" 2>/dev/null)
if [[ -n "$entry" ]]; then
  cached="${entry#*$'\t'}"
  cached_at="${cached##*$'\t'}"
  cached="${cached%$'\t'*}"
  if (( now - cached_at < ttl )); then
    [[ -n "$cached" ]] && echo "$cached"
    exit 0
  fi
fi

if [[ "${3:-}" == "--async" ]]; then
  (
    exec >/dev/null 2>&1 </dev/null
    store "$(compute)"
  ) &
  disown 2>/dev/null
  [[ -n "${cached:-}" ]] && echo "$cached"
else
  result=$(compute)
  store "$result"
  [[ -n "$result" ]] && echo "$result"
fi
