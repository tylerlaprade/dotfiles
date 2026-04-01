#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
cd "$(cat | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0
# TODO: Remove sed workaround once fixed: https://github.com/anthropics/claude-code/issues/21066
# Statusline is blank when output contains ANSI colors (regression in 2.1.x)
git_status=$(git-status-line --async-pr | sed 's/\x1b\]8;[^;]*;[^\x1b]*\x1b\\//g' | sed 's/\x1b\[[0-9;]*m//g')
current_time=$(TZ="America/New_York" date +"%-I:%M %p")
echo "${git_status} · ${current_time}"

# Keep Ghostty tab title current (zsh hooks don't fire during TUI apps)
# Only override title when there's a PR; otherwise let Claude Code's own title persist
if [[ "$git_status" =~ (#[0-9]+\ .*) ]]; then
  printf '\e]0;%s\a' "${BASH_REMATCH[1]}" > /dev/tty 2>/dev/null
fi
