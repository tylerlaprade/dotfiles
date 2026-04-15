#!/usr/bin/env bash
# git co [branch] — interactive fzf branch picker, or plain checkout with arg
set -e

if [ -n "$1" ]; then
  exec git checkout "$@"
fi

branch=$(git branch --sort=-committerdate \
  --format='%(refname:short)	%(committerdate:relative)	%(subject)' | \
  column -t -s $'\t' | \
  fzf --height=40% --reverse \
    --preview='git log --oneline --graph --decorate -15 {1}' \
    --preview-window=right:50%)

if [ -n "$branch" ]; then
  git checkout "$(echo "$branch" | awk '{print $1}')"
fi
