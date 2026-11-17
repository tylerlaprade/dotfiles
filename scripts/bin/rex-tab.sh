#!/bin/bash
# Usage: rex-tab <folder> <command line> [label]
# Types <command line> into a new shell tab (a Rex window) of the one shared Rex
# session labeled Claude, created when missing, and prints the id of the tab's
# block, `block:...`. The tab is labeled <label>, or the folder's name, and
# opens without becoming the active one. Rex's server has no window, so this
# opens none, and neither a Screen Time lockout nor a locked screen holds it.
set -euo pipefail

folder=${1:?folder}
command_line=${2:?command line}
rex=$(rex-path) || { echo "rex-tab: Rex is not installed" >&2; exit 1; }
folder=$(cd "$folder" && pwd)
label=${3:-${folder##*/}}
# Rex takes a leading dash for a flag, and names a window itself given no label.
while [[ "$label" == -* ]]; do label=${label#-}; done
# Inside a Rex terminal, a block in any other session counts as not found.
unset REX_SESSION
shared=Claude
session=$("$rex" ls --json | jq -r --arg label "$shared" \
  'first(.sessions[] | select(.label == $label) | .session_id) // empty')
if [[ -n "$session" ]]; then
  created=$("$rex" window new -s "$session" ${label:+"$label"} -c "$folder" --focus=false --json)
  block=$(jq -er '.block_ids[0]' <<<"$created")
else
  created=$("$rex" new "$shared" ${label:+--window "$label"} -c "$folder" --json)
  block=$(jq -er '.initial_windows[0].block_ids[0]' <<<"$created")
fi
"$rex" send -b "$block" "$command_line"$'\n'
echo "$block"
