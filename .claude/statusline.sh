#!/bin/bash
cd "$(cat | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")
dirty=$(git diff --quiet && git diff --cached --quiet || echo "*")
[[ ${#branch} -gt 40 ]] && branch="${branch:0:20}...${branch: -17}"

# Ahead/behind upstream
read ahead behind < <(git rev-list --left-right --count @{u}...HEAD 2>/dev/null || echo "0 0")
arrows=""
[[ $ahead -gt 0 ]] && arrows+="↓$ahead"
[[ $behind -gt 0 ]] && arrows+="↑$behind"

# Stash indicator
stash=$(git stash list 2>/dev/null | wc -l | tr -d ' ')
[[ $stash -gt 0 ]] && stash="≡" || stash=""

printf "\e[2m%s\e[0m \e[2;%sm%s%s\e[0m\e[2;36m%s%s\e[0m" "$repo" "${dirty:+33}${dirty:-32}" "$branch" "$dirty" "$arrows" "$stash"
