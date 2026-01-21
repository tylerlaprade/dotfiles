#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
cd "$(cat | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0
git-status-line --async-pr
