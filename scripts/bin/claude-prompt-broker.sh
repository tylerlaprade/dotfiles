#!/bin/zsh
# PermissionRequest hook. While a Bossman session is alive, holds each prompt for Bossman and prints its decision.
# With no Bossman alive, or for a Bossman session's own prompt, it prints nothing, so the normal dialog shows.
set -uo pipefail
zmodload zsh/datetime

input=$(cat)
dir=$TMPDIR/claude-prompts
mkdir -p $dir
id=${EPOCHREALTIME/./}-$$
print -r -- "$input" | jq -c --arg id $id '. + {prompt_id: $id}' >> $TMPDIR/claude-permission-prompts.jsonl

session=$(print -r -- "$input" | jq -r .session_id)
bossman=$(jq -r --arg session $session 'select((.name // "") | startswith("Bossman")) | select(.sessionId != $session) | .pid' ~/.claude/sessions/*.json 2>/dev/null | while read -r pid; do kill -0 $pid 2>/dev/null && print $pid; done | head -1)
asker_is_bossman=$(jq -r --arg session $session 'select(.sessionId == $session) | (.name // "") | startswith("Bossman")' ~/.claude/sessions/*.json 2>/dev/null | head -1)
[[ -n $bossman && $asker_is_bossman != true ]] || exit 0

fifo=$dir/$id.fifo
# Claude Code kills this hook when the dialog is answered by hand.
trap 'rm -f $fifo $dir/$id.json; exit 0' TERM HUP INT
mkfifo $fifo
print -r -- "$input" > $dir/$id.json
exec 3<>$fifo
answer=""
while kill -0 $bossman 2>/dev/null; do
  read -r -t 1 answer <&3 && break
done
exec 3<&-
rm -f $fifo $dir/$id.json

case $answer in
  deny\ *) jq -cn --arg message "${answer#deny }" '{hookSpecificOutput: {hookEventName: "PermissionRequest", decision: {behavior: "deny", message: $message}}}' ;;
  ask|"") ;;
  *) print -u2 "claude-prompt-broker: unknown answer: $answer"; exit 1 ;;
esac
