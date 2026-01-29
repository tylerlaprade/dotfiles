#!/bin/bash
# Outputs formatted git status for statuslines
# Usage: git-status-line [--async-pr]
#   --async-pr: Fetch PR number in background (for frequently-called statuslines)

async_pr=false
[[ "$1" == "--async-pr" ]] && async_pr=true

# In zellij: always use session's project. Outside: use current git repo.
if [[ -n "$ZELLIJ_SESSION_NAME" && -d "$HOME/Code/$ZELLIJ_SESSION_NAME/.git" ]]; then
    cd "$HOME/Code/$ZELLIJ_SESSION_NAME" || exit 0
    repo="$ZELLIJ_SESSION_NAME"
elif git_root=$(git rev-parse --show-toplevel 2>/dev/null); then
    repo=$(basename "$git_root")
else
    exit 0
fi
full_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
dirty=$(git diff --quiet && git diff --cached --quiet || echo "*")

# Truncate branch for display
branch="$full_branch"
[[ ${#branch} -gt 120 ]] && branch="${branch:0:60}...${branch: -57}"

# Ahead/behind upstream
read ahead behind < <(git rev-list --left-right --count @{u}...HEAD 2>/dev/null || echo "0 0")
arrows=""
[[ $ahead -gt 0 ]] && arrows+="↓$ahead"
[[ $behind -gt 0 ]] && arrows+="↑$behind"

# Stash indicator
stash=$(git stash list 2>/dev/null | wc -l | tr -d ' ')
[[ $stash -gt 0 ]] && stash="≡" || stash=""

# PR number and state
if $async_pr; then
  pr_data=$(gh-pr-lookup "$repo" "$full_branch" --async)
else
  pr_data=$(gh-pr-lookup "$repo" "$full_branch")
fi
pr_num="${pr_data%%:*}"
pr_state="${pr_data#*:}"

# PR state colors (matching Claude Code)
case "$pr_state" in
  approved)          pr_color=32 ;;  # green
  changes_requested) pr_color=31 ;;  # red
  merged)            pr_color=35 ;;  # purple
  draft)             pr_color=90 ;;  # gray
  *)                 pr_color=33 ;;  # yellow (pending)
esac

# Output with ANSI colors
color=$([[ -n "$dirty" ]] && echo 93 || echo 92)
printf "\e[37m%s\e[0m \e[%sm%s%s\e[0m\e[96m %s%s\e[0m" "$repo" "$color" "$branch" "$dirty" "$arrows" "$stash"
[[ -n "$pr_num" ]] && printf " \e[%sm#%s\e[0m" "$pr_color" "$pr_num"
