#!/bin/bash
# Usage: spawn-session <folder> [brief-file] [claude options...]
# Starts a peer Claude session in <folder>, typed into a shell, and prints the id
# of the terminal it runs in. Without a brief file (no second argument, or one
# starting with "-"), the session starts idle, ready for a cross-session message.
# A brief file wakes it through a SessionStart hook that delivers the brief
# once, passed as spawn-session's own --settings, so a caller with a brief
# cannot pass another. Either way the transcript never records a brief as
# Tyler's own words. Claude is the shell's own command there: run from a
# wrapper, it would keep no transcript.
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

if [[ -n "$brief" ]]; then
  for option in "$@"; do
    if [[ "$option" == --settings || "$option" == --settings=* ]]; then
      echo "spawn-session: a brief is delivered through spawn-session's own --settings, and Claude honors only one; pass the brief or --settings, not both" >&2
      exit 1
    fi
  done
  # SessionStart fires again on /clear and on compaction, so the hook delivers a copy and deletes it.
  delivery=$(mktemp "${TMPDIR:-/tmp}/spawn-brief.XXXXXX")
  cp "$brief" "$delivery"
  printf -v quoted %q "$delivery"
  hook="[ -f $quoted ] || exit 0; cat $quoted >&2; rm $quoted; exit 2"
  settings=$(jq -cn --arg command "$hook" \
    '{hooks: {SessionStart: [{hooks: [{type: "command", command: $command, asyncRewake: true}]}]}}')
  set -- "$@" --settings "$settings"
fi

command_line=claude
for option in "$@"; do
  printf -v command_line '%s %q' "$command_line" "$option"
done

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
  started=$(cd "$folder" && claude --bg "$@")
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
