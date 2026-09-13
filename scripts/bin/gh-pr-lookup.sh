#!/bin/bash

repo="$1"
branch="$2"
async=0
[[ "${3:-}" == "--async" ]] && async=1
[[ -z "$repo" || -z "$branch" ]] && exit 0

background_auth="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../lib/background_auth.py"
pr_map="$HOME/.cache/gh-pr-map"
mkdir -p "$(dirname "$pr_map")"
key="$repo:$branch"
now=$(date +%s)
cache_key=$(printf '%s' "$key" | shasum -a 256)
fetch_lock="${pr_map}.fetch.${cache_key%% *}"

emit_entry() {
  case "$1" in
    __NONE__) ;;
    __KEYCHAIN__) printf '!\tkeychain unavailable\n' ;;
    __LOGIN__) printf '!\tlogin required\n' ;;
    __ERROR__) printf '!\tfetch failed\n' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

refresh() {
  result=$(python3 "$background_auth" github pr list --head "$branch" --limit 1 \
    --json number,title --jq '.[]? | "\(.number)\t\(.title)"' 2>/dev/null)
  case $? in
    0) result="${result:-__NONE__}" ;;
    10) result=__KEYCHAIN__ ;;
    11) result=__LOGIN__ ;;
    *) result=__ERROR__ ;;
  esac
  lock="$pr_map.lock"
  if mkdir "$lock" 2>/dev/null; then
    tmp=$(mktemp "${pr_map}.XXXXXX")
    awk -F '\t' -v key="$key" '$1 != key' "$pr_map" 2>/dev/null > "$tmp"
    printf '%s\t%s\t%s\n' "$key" "$result" "$(date +%s)" >> "$tmp"
    mv "$tmp" "$pr_map"
    rmdir "$lock"
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
  __KEYCHAIN__|__LOGIN__|__ERROR__) ttl=60 ;;
esac

if [[ -n "$cached" && $age -lt $ttl ]]; then
  emit_entry "$cached"
  exit 0
fi

if [[ -d "$fetch_lock" ]]; then
  stamp=$(stat -f %m "$fetch_lock" 2>/dev/null || echo "$now")
  (( now - stamp > 30 )) && rmdir "$fetch_lock" 2>/dev/null
fi

if mkdir "$fetch_lock" 2>/dev/null; then
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
