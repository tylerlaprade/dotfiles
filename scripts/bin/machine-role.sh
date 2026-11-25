#!/bin/bash
# Print this Mac's role, agent or personal, from scripts/setup/machines.conf.
set -euo pipefail

export PATH="$PATH:/usr/sbin"

_source="${BASH_SOURCE[0]}"
while [[ -L "$_source" ]]; do
  _source="$(readlink "$_source")"
done
MACHINES="$(cd "$(dirname "$_source")/../setup" && pwd)/machines.conf"

if ! host_name=$(scutil --get HostName 2>/dev/null); then
  echo "This Mac has no HostName, which names its role in $MACHINES. Run install.sh, which sets it." >&2
  exit 1
fi

role=$(awk -v host="$host_name" '$1 == host { print $2 }' "$MACHINES")
case "$role" in
  agent | personal)
    printf '%s\n' "$role"
    ;;
  "")
    echo "This Mac, $host_name, has no role. Add the line that fits it to $MACHINES:" >&2
    printf '  %s   %s\n' "$host_name" agent "$host_name" personal >&2
    exit 1
    ;;
  *)
    echo "$MACHINES gives $host_name the role \"${role//$'\n'/, }\". It needs exactly one: agent or personal." >&2
    exit 1
    ;;
esac
