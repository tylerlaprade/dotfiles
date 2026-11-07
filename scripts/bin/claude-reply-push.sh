#!/bin/zsh
# Stop hook. When the turn ending began with Tyler's own message, asks Claude to push its reply to his phone, so an
# answer reaches him wherever he wrote from. Turns that peers, task notifications or schedules start push nothing,
# and Claude Code itself skips the push while he is focused on the terminal.
set -uo pipefail

input=$(cat)
fields=$(jq -r '[.stop_hook_active, .transcript_path // ""] | @tsv' <<< "$input") || exit 0
IFS=$'\t' read -r active transcript <<< "$fields"
[[ $active != true && -r $transcript ]] || exit 0
# Reading newest first, the first turn marker is the one for the turn now ending.
origin=$(tail -r -n 4000 "$transcript" | rg -m1 -o '"turnOrigin":"[a-z_]+"')
[[ $origin == '"turnOrigin":"human"' ]] || exit 0
jq -nc --arg reason "This turn began with Tyler's own message. If you already sent a push this turn, stop now. Otherwise call PushNotification with your reply's gist, one line under 200 characters, leading with what he would act on. Write no text at all, before or after the call: your reply above stays the final one, so the push must not be mentioned anywhere." '{decision: "block", reason: $reason}'
