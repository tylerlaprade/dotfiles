#!/bin/bash
# Outputs formatted git status for statuslines

# git-meta caches (repo, repo_full, branch) per-PWD, invalidated by HEAD/config
# mtime. One subprocess on cold call, ~0 on cache hit. Shared with the zsh
# tab-title hook.
meta=$(git-meta 2>/dev/null) || exit 0
IFS=$'\t' read -r repo_name repo_full full_branch <<<"$meta"

# Tracked changes and ahead/behind upstream from one git call. Optional locks
# stay off so a render never contends with another session's git command.
dirty="" ahead=0 behind=0
while read -r kind field head_only upstream_only; do
  if [[ "$kind" == "#" ]]; then
    [[ "$field" == branch.ab ]] && ahead=${head_only#+} behind=${upstream_only#-}
  else
    dirty="*"
  fi
done < <(git --no-optional-locks status --porcelain=v2 --branch --untracked-files=no 2>/dev/null)

# PR number + title (cached indefinitely, tab-separated)
pr_lookup=$(gh-pr-lookup "$repo_name" "$full_branch" --async)
pr_num=${pr_lookup%%$'\t'*}
pr_title=${pr_lookup#*$'\t'}
pr_notice=""
if [[ "$pr_num" == "!" ]]; then
  pr_notice="$pr_title"
  pr_num=""
fi

arrows=""
[[ $behind -gt 0 ]] && arrows+="↓$behind"
[[ $ahead -gt 0 ]] && arrows+="↑$ahead"

stash=""
git rev-parse --quiet --verify refs/stash >/dev/null && stash="≡"

# PR status (uses ETags - free if unchanged)
pr_state=""
pr_ci=""
pr_merge=""
if [[ -n "$pr_num" && -n "$repo_full" ]]; then
  pr_status=$(gh-pr-status "$repo_full" "$pr_num")
  if [[ "$pr_status" == "!"* ]]; then
    pr_notice="${pr_status#*$'\t'}"
  elif [[ -n "$pr_status" ]]; then
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

# Build ref display (PR replaces branch when available)
if [[ -n "$pr_num" ]]; then
  display_title="$pr_title"
  [[ ${#display_title} -gt 80 ]] && display_title="${display_title:0:77}..."
  gt_url="https://app.graphite.dev/github/pr/${repo_full}/${pr_num}"
else
  branch="$full_branch"
  [[ ${#branch} -gt 120 ]] && branch="${branch:0:60}...${branch: -57}"
  branch_color="38;5;242"
fi

# Output with ANSI colors
printf "\e[37m%s\e[0m " "$repo_name"
if [[ -n "$pr_num" ]]; then
  printf '\e]8;;%s\e\\' "$gt_url"
  printf "\e[${pr_color}m#%s\e[0m" "$pr_num"
  [[ -n "$display_title" ]] && printf " \e[37m%s\e[0m" "$display_title"
  printf '\e]8;;\e\\'
else
  printf "\e[${branch_color}m%s\e[0m" "$branch"
  [[ -n "$dirty" ]] && printf "\e[38;5;218m%s\e[0m" "$dirty"
fi
printf "\e[36m %s%s\e[0m%s" "$arrows" "$stash" "$indicators"
[[ -n "$gt_display" ]] && printf " \e[90m%s\e[0m" "$gt_display"

[[ -n "$pr_notice" ]] && printf " \e[33m[PR: %s]\e[0m" "$pr_notice"
