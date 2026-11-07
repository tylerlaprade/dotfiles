#!/bin/bash
# Usage: spawn-session <folder> <brief-file> [claude options...]
# Starts a peer Claude session in <folder> with the brief as its first prompt,
# typed into a shell, and prints the id of the terminal it runs in. Claude is the
# shell's own command there: run from a wrapper, it would keep no transcript.
# With Rex installed, the terminal is a new Rex session and the id is its block,
# `block:...`. Rex's server has no window, so this opens none and neither a
# lockout nor a locked screen holds it.
# Otherwise it is a new tab from ghostty-tab, and the id is the tab's. While
# Screen Time has Ghostty locked out, or that is not yet known, the session
# starts with `claude --bg` instead, this prints "background <id>", and
# session-guard opens its tab once the lockout lifts.
set -euo pipefail

folder=${1:?folder}
brief=${2:?brief file}
shift 2
[[ -r "$brief" ]] || { echo "spawn-session: cannot read $brief" >&2; exit 1; }
[[ -d "$folder" ]] || { echo "spawn-session: no folder $folder" >&2; exit 1; }
[[ "$brief" == /* ]] || brief="$PWD/$brief"

command_line=claude
for option in "$@"; do
  printf -v command_line '%s %q' "$command_line" "$option"
done
printf -v command_line "%s \"\$(cat %q)\"" "$command_line" "$brief"

if rex=$(rex-path 2>/dev/null); then
  folder=$(cd "$folder" && pwd)
  label=${folder##*/}
  while (( $# > 0 )); do
    case $1 in
      --name | -n) label=${2:-$label}; break ;;
      --name=*) label=${1#--name=}; break ;;
      *) shift ;;
    esac
  done
  # Rex takes a leading dash for a flag, and names a session itself given no label.
  while [[ "$label" == -* ]]; do label=${label#-}; done
  # Inside a Rex terminal, a block in any other session counts as not found.
  unset REX_SESSION
  created=$("$rex" new ${label:+"$label"} -c "$folder" --json)
  block=$(jq -er '.initial_windows[0].block_ids[0]' <<<"$created")
  "$rex" send -b "$block" "$command_line"$'\n'
  echo "$block"
  exit 0
fi

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

exec ghostty-tab "$folder" "$command_line"
