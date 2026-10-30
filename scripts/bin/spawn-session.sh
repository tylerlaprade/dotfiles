#!/bin/bash
# Usage: spawn-session <folder> <brief-file> [claude options...]
# Starts a peer Claude session in <folder> with the brief as its first prompt,
# typed into a new tab from ghostty-tab, and prints the tab's id. Claude is the
# shell's own command there: run from a wrapper, it would keep no transcript.
# While Screen Time has Ghostty locked out, or that is not yet known, the
# session starts with `claude --bg` instead, this prints "background <id>", and
# session-guard opens its tab once the lockout lifts.
set -euo pipefail

folder=${1:?folder}
brief=${2:?brief file}
shift 2
[[ -r "$brief" ]] || { echo "spawn-session: cannot read $brief" >&2; exit 1; }

guard="$HOME/.cargo/bin/session-guard"
if ! "$guard" lockout >/dev/null 2>&1; then
  started=$(cd "$folder" && claude --bg "$@" "$(<"$brief")")
  backgrounded='backgrounded · ([0-9a-f]+)'
  if [[ ! "$started" =~ $backgrounded ]]; then
    printf 'spawn-session: claude --bg printed no id:\n%s\n' "$started" >&2
    exit 1
  fi
  session=${BASH_REMATCH[1]}
  "$guard" attach-after-lockout "$session"
  echo "background $session"
  exit 0
fi

command_line=claude
for option in "$@"; do
  printf -v command_line '%s %q' "$command_line" "$option"
done
printf -v command_line "%s \"\$(cat %q)\"" "$command_line" "$brief"
exec ghostty-tab "$folder" "$command_line"
