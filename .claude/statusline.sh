#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
cd "$(cat | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0
# TODO: Remove sed workaround once fixed: https://github.com/anthropics/claude-code/issues/21066
# Statusline is blank when output contains ANSI colors (regression in 2.1.x)
git-status-line --async-pr | sed 's/\x1b\[[0-9;]*m//g'
