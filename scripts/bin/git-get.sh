#!/usr/bin/env bash
# git get <branch|PR#> — fetch the target from origin, update it locally, then walk its stack
# via GitHub PR base refs and rebase each branch onto its parent. Zero dependency on gt.
set -e

if [ -z "$1" ]; then
  echo "Usage: git get <branch|PR#>" >&2
  exit 1
fi

target="$1"
trunk=main

echo "Fetching origin..."
git fetch origin --prune

# Resolve PR number to branch name
if [[ "$target" =~ ^[0-9]+$ ]]; then
  branch=$(gh pr view "$target" --json headRefName --jq .headRefName 2>/dev/null)
  if [ -z "$branch" ]; then
    echo "PR #$target not found." >&2
    exit 1
  fi
  target="$branch"
fi

# Checkout / update target to match remote
if git show-ref --verify --quiet "refs/heads/$target"; then
  git checkout "$target"
  if git show-ref --verify --quiet "refs/remotes/origin/$target"; then
    git reset --hard "origin/$target"
  fi
else
  git checkout -b "$target" "origin/$target"
fi

# Batch-load all my PRs
all_prs=$(gh pr list --state all --limit 200 --author @me \
  --json number,state,baseRefName,headRefName 2>/dev/null || echo "[]")

pr_base() {
  echo "$all_prs" | jq -r --arg b "$1" \
    '[.[] | select(.headRefName == $b)] | sort_by(.number) | last | .baseRefName // empty'
}
pr_state() {
  echo "$all_prs" | jq -r --arg b "$1" \
    '[.[] | select(.headRefName == $b)] | sort_by(.number) | last | .state // empty'
}

# Walk stack from target up to trunk
chain=()
seen=""
cursor="$target"
while [ "$cursor" != "$trunk" ]; do
  case " $seen " in *" $cursor "*) break ;; esac
  seen="$seen $cursor"

  parent=$(pr_base "$cursor")
  [ -z "$parent" ] && parent="$trunk"

  while [ "$parent" != "$trunk" ]; do
    pstate=$(pr_state "$parent")
    if [ "$pstate" = "MERGED" ] || [ "$pstate" = "CLOSED" ]; then
      grand=$(pr_base "$parent")
      [ -z "$grand" ] && grand="$trunk"
      parent="$grand"
    else
      break
    fi
  done

  chain=("$cursor:$parent" "${chain[@]}")
  [ "$parent" = "$trunk" ] && break
  cursor="$parent"
done

# Update trunk
echo "Updating $trunk..."
git fetch origin "$trunk:$trunk" 2>/dev/null || git branch -f "$trunk" "origin/$trunk"

# Rebase stack bottom-up
for entry in "${chain[@]}"; do
  branch="${entry%%:*}"
  parent="${entry##*:}"

  echo ""
  echo "Rebasing $branch onto $parent..."
  git checkout "$branch"
  if ! git rebase "$parent"; then
    echo ""
    echo "Conflict rebasing $branch onto $parent."
    echo "Resolve files, 'git add', then 'git rebase --continue'."
    exit 1
  fi
done

# Land on target
if [ "$(git branch --show-current)" != "$target" ]; then
  git checkout "$target"
fi
