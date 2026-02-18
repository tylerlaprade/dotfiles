#!/bin/bash
# Outputs formatted git status for statuslines

# In zellij: always use session's project. Outside: use current git repo.
if [[ -n "$ZELLIJ_SESSION_NAME" && -d "$HOME/Code/$ZELLIJ_SESSION_NAME/.git" ]]; then
    cd "$HOME/Code/$ZELLIJ_SESSION_NAME" || exit 0
    repo_name="$ZELLIJ_SESSION_NAME"
elif git_root=$(git rev-parse --show-toplevel 2>/dev/null); then
    repo_name=$(basename "$git_root")
else
    exit 0
fi

repo_full=$(git remote get-url origin 2>/dev/null | sed -E 's#(git@|https://)github\.com[:/]##; s#\.git$##')
full_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
dirty=$(git diff --quiet && git diff --cached --quiet || echo "*")

# PR number + title (cached indefinitely, tab-separated)
pr_lookup=$(gh-pr-lookup "$repo_name" "$full_branch" --async)
pr_num=$(echo "$pr_lookup" | cut -f1)
pr_title=$(echo "$pr_lookup" | cut -f2-)

# Truncate branch for display (only shown when no PR)
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

# PR status (uses ETags - free if unchanged)
pr_state=""
pr_ci=""
pr_merge=""
if [[ -n "$pr_num" && -n "$repo_full" ]]; then
  pr_status=$(gh-pr-status "$repo_full" "$pr_num")
  if [[ -n "$pr_status" ]]; then
    pr_state="${pr_status%%:*}"
    rest="${pr_status#*:}"
    pr_ci="${rest%%:*}"
    pr_merge="${rest#*:}"
  fi
fi

# PR state colors
case "$pr_state" in
  approved)          pr_color=32 ;;  # green
  changes_requested) pr_color=31 ;;  # red
  merged)            pr_color=35 ;;  # purple
  draft)             pr_color=90 ;;  # gray
  *)                 pr_color=33 ;;  # yellow (pending)
esac

# Secondary indicators (both if applicable, red)
indicators=""
[[ "$pr_merge" == "conflict" ]] && indicators+=$'\e[31m!\e[0m'
[[ "$pr_ci" == "fail" ]] && indicators+=$'\e[31m✗\e[0m'
[[ -n "$indicators" ]] && indicators=" $indicators"

# Graphite status (cached, async on new branch)
gt_info=$(gt-status "$repo_name" "$full_branch" --async)
gt_display=""
if [[ -n "$gt_info" ]]; then
  IFS=: read gt_total gt_depth gt_unsub <<< "$gt_info"
  gt_display="⎇$gt_total"
  [[ $gt_depth -gt 0 ]] && gt_display+="↕$gt_depth"
  [[ $gt_unsub -gt 0 ]] && gt_display+="◌$gt_unsub"
fi

# Output with ANSI colors
color=$([[ -n "$dirty" ]] && echo 93 || echo 92)
printf "\e[37m%s\e[0m" "$repo_name"
if [[ -n "$pr_num" ]]; then
  # Skip branch when PR exists — show more of the title instead
  printf "\e[96m %s%s\e[0m" "$arrows" "$stash"
  display_title="$pr_title"
  [[ ${#display_title} -gt 80 ]] && display_title="${display_title:0:77}..."
  printf " \e[%sm#%s\e[0m%s" "$pr_color" "$pr_num" "$indicators"
  [[ -n "$display_title" ]] && printf " \e[37m%s\e[0m" "$display_title"
else
  printf " \e[%sm%s%s\e[0m\e[96m %s%s\e[0m" "$color" "$branch" "$dirty" "$arrows" "$stash"
fi
[[ -n "$gt_display" ]] && printf " \e[90m%s\e[0m" "$gt_display"
