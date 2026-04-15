#!/usr/bin/env bash
# git pr [branch|PR#] — open PR page in browser. Handles fork PRs (which gh pr view can't find by branch).
set -e

target="${1:-$(git branch --show-current)}"

if [[ "$target" =~ ^[0-9]+$ ]]; then
  gh pr view "$target" --web
  exit
fi

# Look up by branch via pr list (works for fork PRs)
pr_num=$(gh pr list --state all --head "$target" --limit 1 --json number --jq '.[0].number' 2>/dev/null)

if [ -z "$pr_num" ] || [ "$pr_num" = "null" ]; then
  echo "No PR found for $target" >&2
  exit 1
fi

gh pr view "$pr_num" --web
