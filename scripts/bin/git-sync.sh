#!/usr/bin/env bash
# git sync — fetch origin, walk the stack via GitHub PR base refs, rebase each branch onto its parent,
# clean up merged/closed branches. Zero dependency on gt.
set -e

force=false
while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force) force=true; shift ;;
    *) shift ;;
  esac
done

trunk=main
orig=$(git branch --show-current)

echo "Fetching origin..."
git fetch origin --prune

# Fetch all recent PRs once so stack walking doesn't hit the API repeatedly
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

# Walk from orig up to trunk. For each branch, record its "effective parent"
# (nearest non-merged/closed ancestor, or trunk).
chain=()
seen=""
cursor="$orig"
while [ "$cursor" != "$trunk" ]; do
  case " $seen " in *" $cursor "*) break ;; esac
  seen="$seen $cursor"

  parent=$(pr_base "$cursor")
  [ -z "$parent" ] && parent="$trunk"

  # Resolve parent through any merged/closed PRs
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

  chain=("$cursor:$parent" "${chain[@]}")  # prepend → bottom-up order
  [ "$parent" = "$trunk" ] && break
  cursor="$parent"
done

# Update trunk
echo "Updating $trunk..."
if [ "$orig" = "$trunk" ]; then
  git reset --hard "origin/$trunk"
else
  git fetch origin "$trunk:$trunk" 2>/dev/null || git branch -f "$trunk" "origin/$trunk"
fi

# Rebase stack bottom-up. Each iteration's parent is either trunk (already updated)
# or a branch we updated in a previous iteration.
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

# Return to original branch
if [ "$(git branch --show-current)" != "$orig" ]; then
  git checkout "$orig"
fi

# Cleanup: branches with only merged/closed PRs (no open PR)
echo ""
echo "Checking for merged branches..."
open_prs=$(echo "$all_prs" | jq -r '.[] | select(.state == "OPEN") | .headRefName' | sort -u)
closed_prs=$(echo "$all_prs" | jq -r '.[] | select(.state == "MERGED" or .state == "CLOSED") | .headRefName' | sort -u)

merged=()
for branch in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  [ "$branch" = "$trunk" ] && continue
  if echo "$open_prs" | grep -qx "$branch"; then continue; fi
  if echo "$closed_prs" | grep -qx "$branch"; then merged+=("$branch"); fi
done

if [ ${#merged[@]} -eq 0 ]; then
  echo "No merged/closed branches."
  exit 0
fi

echo "Merged/closed branches:"
printf '  %s\n' "${merged[@]}"

if [ "$force" = true ]; then
  for branch in "${merged[@]}"; do
    echo "Deleting $branch..."
    git branch -D "$branch"
  done
else
  printf '\nDelete all? [y/N] '
  read -r answer
  if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
    for branch in "${merged[@]}"; do
      git branch -D "$branch"
    done
  fi
fi
