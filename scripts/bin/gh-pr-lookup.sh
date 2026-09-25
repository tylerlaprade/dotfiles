#!/bin/bash
# Look up PR number + title for a branch, using persistent cache with TTL.
# Usage: gh-pr-lookup <repo> <branch> [--async]
# Outputs: number\ttitle, nothing when there is no PR, or !\t<notice>.
# Cache format: <key>\t<result-or-sentinel>\t<unix-ts>
# TTL: real PRs 300s; "no PR" 30s so a fresh PR appears within ~30s;
# failures 60s. Stale entries are served while a detached refresh runs.

repo="$1"
branch="$2"
async=0
[[ "${3:-}" == "--async" ]] && async=1
[[ -z "$repo" || -z "$branch" ]] && exit 0

pr_map="$HOME/.cache/gh-pr-map"
key="$repo:$branch"
now=$(date +%s)
write_lock="$pr_map.lock"

# mkdir is atomic. A refresh killed mid-flight leaves its lock behind, so a
# lock older than max_age is leftover, not in-flight.
claim_lock() {
  local lock=$1 max_age=$2 stamp
  mkdir "$lock" 2>/dev/null && return 0
  stamp=$(stat -f %m "$lock" 2>/dev/null) || return 1
  (( now - stamp > max_age )) || return 1
  rmdir "$lock" 2>/dev/null
  mkdir "$lock" 2>/dev/null
}

emit_entry() {
  case "$1" in
    __NONE__) ;;
    __LOGIN__) printf '!\tlogin required\n' ;;
    __ERROR__) printf '!\tfetch failed\n' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

refresh() {
  result=$(gh-background pr list --head "$branch" --limit 1 \
    --json number,title --jq '.[]? | "\(.number)\t\(.title)"')
  case $? in
    0) result="${result:-__NONE__}" ;;
    11) result=__LOGIN__ ;;
    *) result=__ERROR__ ;;
  esac
  if claim_lock "$write_lock" 30; then
    tmp=$(mktemp "${pr_map}.XXXXXX")
    awk -F '\t' -v key="$key" '$1 != key' "$pr_map" 2>/dev/null > "$tmp"
    printf '%s\t%s\t%s\n' "$key" "$result" "$(date +%s)" >> "$tmp"
    mv "$tmp" "$pr_map"
    rmdir "$write_lock"
  fi
  rmdir "$fetch_lock"
}

entry=$(awk -F '\t' -v key="$key" '$1 == key { print; exit }' "$pr_map" 2>/dev/null)
cached=""
age=300
if [[ -n "$entry" ]]; then
  cached="${entry#*$'\t'}"
  ts="${cached##*$'\t'}"
  cached="${cached%$'\t'*}"
  age=$(( now - ts ))
fi

ttl=300
case "$cached" in
  __NONE__) ttl=30 ;;
  __LOGIN__|__ERROR__) ttl=60 ;;
esac

if [[ -n "$cached" && $age -lt $ttl ]]; then
  emit_entry "$cached"
  exit 0
fi

mkdir -p "${pr_map%/*}"
cache_key=$(printf '%s' "$key" | shasum -a 256)
fetch_lock="${pr_map}.fetch.${cache_key%% *}"
if claim_lock "$fetch_lock" 30; then
  if (( async )); then
    (
      exec >/dev/null 2>&1 </dev/null
      refresh
    ) &
    disown 2>/dev/null
  else
    refresh
    emit_entry "$result"
    exit 0
  fi
fi

if [[ -n "$cached" ]]; then
  emit_entry "$cached"
else
  printf '!\tchecking\n'
fi
