#!/bin/bash
# Usage: kid-lock [--wait]
# Exits 0 when kid mode is off, 1 while Kanata's kid layer is active, and 2 when
# the layer cannot be read. With --wait, blocks until kid mode is off.
# Kanata greets each new TCP client with a LayerChange naming the current layer,
# then sends one on every change, so the answer comes from Kanata itself.
set -u

kid_layer=kid
wait_for_unlock=false
[[ "${1:-}" == --wait ]] && wait_for_unlock=true

plist=${KID_LOCK_KANATA_PLIST:-/Library/LaunchDaemons/com.tylerlaprade.kanata.plist}
arguments=$(plutil -extract ProgramArguments json -o - "$plist" 2>/dev/null) || exit 2
address=$(jq -r 'index("--port") as $flag | if $flag then .[$flag + 1] else empty end' <<<"$arguments") || exit 2
[[ "$address" == *:* ]] || exit 2

# Kid mode is a Kanata layer, so it cannot be on while Kanata is not listening.
{ exec 3<>"/dev/tcp/${address%:*}/${address##*:}"; } 2>/dev/null || exit 0

read_message() {
  if [[ "$wait_for_unlock" == true ]]; then
    IFS= read -r message <&3
  else
    IFS= read -r -t 5 message <&3
  fi
}

while read_message; do
  layer=$(jq -r '.LayerChange.new // empty' <<<"$message" 2>/dev/null) || exit 2
  [[ -z "$layer" ]] && continue
  [[ "$layer" != "$kid_layer" ]] && exit 0
  [[ "$wait_for_unlock" == true ]] || exit 1
done
# A restarted Kanata starts on its base layer, so waiting resumes on the new connection.
[[ "$wait_for_unlock" == true ]] && exec "$0" --wait
exit 2
