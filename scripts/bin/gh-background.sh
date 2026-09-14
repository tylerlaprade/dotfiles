#!/bin/bash
# Run gh for a background status check: prompts off, 8s deadline.
# Exit: 0 ok, 11 login required, 12 request failed.
# gh stores and reads its token through /usr/bin/security, so the read never
# opens a macOS password dialog and survives gh, Python, and OS upgrades.

stderr=$(mktemp)
trap 'rm -f "$stderr"' EXIT
GH_PROMPT_DISABLED=1 timeout 8 gh "$@" 2>"$stderr"
status=$?
[ "$status" -eq 0 ] && exit 0
if [ "$status" -eq 4 ] || grep -q -E 'HTTP 401|Bad credentials' "$stderr"; then
  exit 11
fi
exit 12
