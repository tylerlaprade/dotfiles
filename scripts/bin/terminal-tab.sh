#!/bin/bash
# Usage: terminal-tab <folder> <command line> [automation owner executable]
# Types <command line> into a new shell tab and prints the tab's id: a Rex tab
# from rex-tab when Rex is installed, otherwise a Ghostty tab from ghostty-tab,
# the only one that needs an automation owner.
set -euo pipefail

if rex-path >/dev/null 2>&1; then
  exec rex-tab "${1:?folder}" "${2:?command line}"
fi
exec ghostty-tab "$@"
