#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
input=$(cat)
cd "$(echo "$input" | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0

tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0 | round')

RED='\033[31m'
YELLOW='\033[33m'
GREEN='\033[32m'
RESET='\033[0m'

ctx_info=""
if [ "$tokens" -gt 0 ] 2>/dev/null; then
  # Smooth color gradient based on context degradation research:
  #   0-25%: flat green (minimal degradation)
  #  25-75%: green → yellow (gradual degradation)
  #  75-95%: yellow → red (significant quality loss)
  # 95-100%: intense red
  if [ "$pct" -le 25 ]; then
    r=0 g=200 b=0
  elif [ "$pct" -le 75 ]; then
    t=$(( (pct - 25) * 100 / 50 ))
    r=$(( 255 * t / 100 ))
    g=200
    b=0
  elif [ "$pct" -le 95 ]; then
    t=$(( (pct - 75) * 100 / 20 ))
    r=255
    g=$(( 200 - 200 * t / 100 ))
    b=0
  else
    r=255 g=0 b=0
  fi
  bar_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")

  # Build 10-char progress bar
  filled=$((pct * 10 / 100))
  [ "$filled" -gt 10 ] && filled=10
  empty=$((10 - filled))
  bar=""
  [ "$filled" -gt 0 ] && printf -v fill "%${filled}s" && bar="${fill// /▓}"
  [ "$empty" -gt 0 ] && printf -v pad "%${empty}s" && bar="${bar}${pad// /░}"
  ctx_info="Context ${bar_color}${bar} ${pct}%${RESET}"
fi

# Rate limit info
rate_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty | round')
rate_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty | round')
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

# Line 1: context bar · rates · time
parts=()
[ -n "$ctx_info" ] && parts+=("$ctx_info")
rate=$(format_rate "$rate_5h" "$resets_5h")
[ -n "$rate" ] && parts+=("5-hour $rate")
rate=$(format_rate "$rate_7d" "$resets_7d")
[ -n "$rate" ] && parts+=("7-day $rate")
WHITE='\033[97m'
parts+=("${WHITE}${current_time}${RESET}")

echo -e "$(printf '%s' "${parts[0]}")$(printf ' · %s' "${parts[@]:1}")"

# Line 2: git info
echo -e "$git_status"

# Keep Ghostty tab title current (zsh hooks don't fire during TUI apps)
# Only override title when there's a PR; otherwise let Claude Code's own title persist
if [[ "$git_status" =~ (#[0-9]+\ .*) ]]; then
  printf '\e]0;%s\a' "${BASH_REMATCH[1]}" > /dev/tty 2>/dev/null
fi
