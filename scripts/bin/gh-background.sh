#!/bin/bash
# Run gh for a background status check: prompts off, 8s deadline.
# Exit: 0 ok, 11 login required, 12 request failed.

script_path=$(realpath "${BASH_SOURCE[0]}")
keychain_check="$(dirname "$script_path")/../keychain-unlocked.py"
if [ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}${GH_ENTERPRISE_TOKEN:-}${GITHUB_ENTERPRISE_TOKEN:-}" ]; then
  python3 "$keychain_check" 2>/dev/null || exit 12
fi

stderr=$(mktemp)
trap 'rm -f "$stderr"' EXIT
GH_PROMPT_DISABLED=1 timeout 8 gh "$@" 2>"$stderr"
status=$?
[ "$status" -eq 0 ] && exit 0
if [ "$status" -eq 4 ] || grep -q -E 'HTTP 401|Bad credentials' "$stderr"; then
  exit 11
fi
exit 12
