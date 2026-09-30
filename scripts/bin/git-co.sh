#!/usr/bin/env bash
# git co [branch] — interactive fzf branch picker, or plain checkout with arg
set -e

if [[ -n "$1" ]]; then
  exec git checkout "$@"
fi

branch_rows=$(git branch --sort=-committerdate \
  --format='%(refname:short)	%(committerdate:relative)	%(subject)')
aligned_rows=$(column -t -s $'\t' <<< "$branch_rows")
branch=$(fzf --height=40% --reverse \
  --preview='git log --oneline --graph --decorate -15 {1}' \
  --preview-window=right:50% <<< "$aligned_rows")

if [[ -n "$branch" ]]; then
  git checkout "${branch%% *}"
fi
