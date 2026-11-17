#!/bin/bash
# Usage: spawn-session <folder> [brief-file] [claude options...]
# Starts a peer Claude session in <folder>, typed into a shell, and prints the id
# of the terminal it runs in. A brief file becomes its first prompt. Without one
# (no second argument, or one starting with "-"), the session starts idle, ready
# for a cross-session message, so the transcript never shows another session's
# brief as Tyler's own words. Claude is the shell's own command there: run from
# a wrapper, it would keep no transcript.
# With Rex installed, the terminal is a new tab from rex-tab, labeled with the
# session's --name or else its folder's name, and the id is its block.
# Otherwise it is a new tab from ghostty-tab, and the id is the tab's. While
# Screen Time has Ghostty locked out, or that is not yet known, the session
# starts with `claude --bg` instead, this prints "background <id>", and
# session-guard opens its tab once the lockout lifts.
set -euo pipefail

folder=${1:?folder}
shift
brief=
if (( $# > 0 )) && [[ "$1" != -* ]]; then
  brief=$1
  shift
  [[ -r "$brief" ]] || { echo "spawn-session: cannot read $brief" >&2; exit 1; }
fi
[[ -d "$folder" ]] || { echo "spawn-session: no folder $folder" >&2; exit 1; }
[[ -z "$brief" || "$brief" == /* ]] || brief="$PWD/$brief"

command_line=claude
for option in "$@"; do
  printf -v command_line '%s %q' "$command_line" "$option"
done
if [[ -n "$brief" ]]; then
  printf -v command_line "%s \"\$(cat %q)\"" "$command_line" "$brief"
fi

if rex-path >/dev/null 2>&1; then
  label=
  while (( $# > 0 )); do
    case $1 in
      --name | -n) label=${2:-}; break ;;
      --name=*) label=${1#--name=}; break ;;
      *) shift ;;
    esac
  done
  exec rex-tab "$folder" "$command_line" "$label"
fi

guard="$HOME/.cargo/bin/session-guard"
if ! "$guard" lockout >/dev/null 2>&1; then
  started=$(cd "$folder" && claude --bg "$@" ${brief:+"$(<"$brief")"})
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
