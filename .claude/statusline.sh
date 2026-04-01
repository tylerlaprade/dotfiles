#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
input=$(cat)
cd "$(echo "$input" | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0

# Context window degradation warning (absolute token thresholds)
tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)

RED='\033[31m'
YELLOW='\033[33m'
GREEN='\033[32m'
RESET='\033[0m'

ctx_info=""
if [ "$tokens" -gt 0 ] 2>/dev/null; then
  if [ "$tokens" -ge 180000 ]; then
    ctx_info="${RED}Context ${pct}% !!!${RESET}"
  elif [ "$tokens" -ge 150000 ]; then
    ctx_info="${RED}Context ${pct}% !!${RESET}"
  elif [ "$tokens" -ge 100000 ]; then
    ctx_info="${YELLOW}Context ${pct}% !${RESET}"
  else
    ctx_info="${GREEN}Context ${pct}%${RESET}"
  fi
fi

# Rate limit info
rate_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
rate_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
resets_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
resets_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

format_reset() {
  [ -z "$1" ] && return
  local reset_epoch=$1
  local now=$(date +%s)
  local diff=$(( reset_epoch - now ))
  if [ "$diff" -lt 86400 ]; then
    TZ="America/New_York" date -r "$reset_epoch" +"%-I:%M %p" 2>/dev/null
  else
    TZ="America/New_York" date -r "$reset_epoch" +"%a %-I:%M %p" 2>/dev/null
  fi
}

format_rate() {
  local pct=$1 resets=$2
  [ -z "$pct" ] && return
  local color=$GREEN
  [ "$pct" -ge 75 ] 2>/dev/null && color=$YELLOW
  [ "$pct" -ge 90 ] 2>/dev/null && color=$RED
  local info="${color}${pct}%${RESET}"
  local reset=$(format_reset "$resets")
  [ -n "$reset" ] && info="${info} (resets ${reset})"
  echo "$info"
}

git_status=$(git-status-line --async-pr)
current_time=$(TZ="America/New_York" date +"%-I:%M %p")

parts=("$git_status")
[ -n "$ctx_info" ] && parts+=("$ctx_info")
rate=$(format_rate "$rate_5h" "$resets_5h")
[ -n "$rate" ] && parts+=("5-hour $rate")
rate=$(format_rate "$rate_7d" "$resets_7d")
[ -n "$rate" ] && parts+=("7-day $rate")
WHITE='\033[97m'
parts+=("${WHITE}${current_time}${RESET}")

echo -e "$(printf '%s' "${parts[0]}")$(printf ' · %s' "${parts[@]:1}")"

# Keep Ghostty tab title current (zsh hooks don't fire during TUI apps)
# Only override title when there's a PR; otherwise let Claude Code's own title persist
if [[ "$git_status" =~ (#[0-9]+\ .*) ]]; then
  printf '\e]0;%s\a' "${BASH_REMATCH[1]}" > /dev/tty 2>/dev/null
fi
