#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
input=$(cat)
cd "$(echo "$input" | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0

pct=$(echo "$input" | jq -r '.context_window | ((.used_percentage // 0) * (.context_window_size // 200000) / 200000) | round')

RESET='\033[0m'

# Tomorrow Night gradient: green → yellow → red with asymptotic red tail
# Sets global r, g, b. Args: value green_end yellow_point red_point [asymptotic_k]
tn_gradient() {
  local val=$1 green_end=$2 yellow_pt=$3 red_pt=$4 k=${5:-80}
  if [ "$val" -le "$green_end" ]; then
    r=181 g=189 b=104
  elif [ "$val" -le "$yellow_pt" ]; then
    local t=$(( (val - green_end) * 100 / (yellow_pt - green_end) ))
    r=$(( 181 + (240 - 181) * t / 100 ))
    g=$(( 189 + (198 - 189) * t / 100 ))
    b=$(( 104 + (116 - 104) * t / 100 ))
  elif [ "$val" -le "$red_pt" ]; then
    local t=$(( (val - yellow_pt) * 100 / (red_pt - yellow_pt) ))
    r=$(( 240 + (204 - 240) * t / 100 ))
    g=$(( 198 + (102 - 198) * t / 100 ))
    b=$(( 116 + (102 - 116) * t / 100 ))
  else
    local t=$(( (val - red_pt) * 100 / (val - red_pt + k) ))
    r=$(( 204 + (255 - 204) * t / 100 ))
    g=$(( 102 - 102 * t / 100 ))
    b=$(( 102 - 102 * t / 100 ))
  fi
}

# Smooth color gradient based on context degradation research:
#   0-25%: flat green (minimal degradation)
#  25-60%: green → yellow (gradual degradation)
#  60-85%: yellow → red (significant quality loss)
#    85%+: asymptotic intense red
if [ "$pct" -le 25 ]; then
  r=0 g=200 b=0
elif [ "$pct" -le 60 ]; then
  t=$(( (pct - 25) * 100 / 35 ))
  r=$(( 255 * t / 100 ))
  g=200
  b=0
elif [ "$pct" -le 85 ]; then
  t=$(( (pct - 60) * 100 / 25 ))
  r=255
  g=$(( 200 - 200 * t / 100 ))
  b=0
else
  t=$(( (pct - 85) * 100 / (pct - 85 + 30) ))
  r=255
  g=0
  b=0
fi
bar_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")

# Build 10-char progress bar with smooth transition square
filled=$((pct * 10 / 100))
[ "$filled" -gt 10 ] && filled=10
frac=$((pct * 10 % 100))

bar=""
[ "$filled" -gt 0 ] && printf -v fill "%${filled}s" && bar="${bar_color}${fill// /▓}"

if [ "$filled" -lt 10 ]; then
  if [ "$frac" -lt 50 ]; then
    bar="${bar}${bar_color}░"
  else
    bar="${bar}${bar_color}▒"
  fi

  empty=$((9 - filled))
  [ "$empty" -gt 0 ] && printf -v pad "%${empty}s" && bar="${bar}${pad// /░}"
fi
ctx_info="${bar}${bar_color} ${pct}%${RESET}"

# Rate limit info
rate_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty | round')
rate_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty | round')
resets_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
resets_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

format_rate() {
  local pct=$1 resets=$2 window_secs=$3 display_override=$4 display_color=$5
  [ -z "$pct" ] && return

  local now=$(date +%s)
  local time_remaining=$(( resets - now ))
  [ "$time_remaining" -lt 0 ] && time_remaining=0
  local time_elapsed=$(( window_secs - time_remaining ))
  [ "$time_elapsed" -lt 60 ] && time_elapsed=60

  local info
  if [ -n "$display_override" ]; then
    info="${display_color}${display_override}${RESET}"
  else
    # Absolute usage gradient: 0-55% green, 55-80% green→yellow, 80-100% yellow→red
    tn_gradient "$pct" 55 80 100
    local pct_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    local suffix="%"; [ "$pct" -ge 100 ] && suffix="%+"
    info="${pct_color}${pct}${suffix}${RESET}"
  fi

  # Pace ratio for time remaining color (Ghostty Tomorrow Night palette)
  # Sigmoid-like piecewise: steep transition around 0.8x, 1.0x is red
  # green(181,189,104) → yellow(240,198,116) → red(204,102,102)
  local time_elapsed_pct=$(( time_elapsed * 100 / window_secs ))
  local ratio
  if [ "$time_elapsed_pct" -gt 0 ]; then
    ratio=$(( pct * 100 / time_elapsed_pct ))
  else
    ratio=100
  fi

  # Pace gradient for time remaining
  #   ≤0.75x: flat green, 0.75-0.98x: green→yellow, 0.98-1.25x: yellow→red, >1.25x: asymptotic red
  tn_gradient "$ratio" 75 98 125
  local time_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")

  if [ "$time_remaining" -gt 0 ]; then
    local reset_str
    if [ "$time_remaining" -lt 86400 ]; then
      reset_str=$(TZ="America/New_York" date -r "$resets" +"%-I:%M %p" 2>/dev/null)
    else
      reset_str=$(TZ="America/New_York" date -r "$resets" +"%a %-I:%M %p" 2>/dev/null)
    fi
    local days=$(( time_remaining / 86400 ))
    local hrs=$(( (time_remaining % 86400) / 3600 ))
    local mins=$(( (time_remaining % 3600) / 60 ))
    local remaining=""
    if [ "$days" -gt 0 ]; then
      remaining="${days}d ${hrs}h"
    elif [ "$hrs" -gt 0 ]; then
      remaining="${hrs}h ${mins}m"
    else
      remaining="${mins}m"
    fi
    [ -n "$reset_str" ] && info="${info} (resets in ${time_color}${remaining}${RESET} at ${reset_str})"
  fi

  echo "$info"
}

git_status=$(git-status-line --async-pr)
current_time=$(TZ="America/New_York" date +"%-I:%M %p")

# Line 1: context bar · rates · time
parts=()
parts+=("$ctx_info")
cost_display="" cost_color=""
if [ "${rate_5h:-0}" -ge 100 ]; then
  raw_cost=$(echo "$input" | jq -r '.cost.total_cost_usd // empty')
  if [ -n "$raw_cost" ]; then
    session_cost=$(printf "%.2f" "$raw_cost")
    cost_cents=$(echo "$input" | jq -r '(.cost.total_cost_usd // 0) * 100 | round')
    # Asymptotic red from 100%-red base: (204,102,102) → (255,0,0)
    t=$(( cost_cents * 100 / (cost_cents + 2000) ))
    r=$(( 204 + (255 - 204) * t / 100 ))
    g=$(( 102 - 102 * t / 100 ))
    b=$(( 102 - 102 * t / 100 ))
    cost_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    cost_display="\$${session_cost}"
  fi
fi
rate=$(format_rate "$rate_5h" "$resets_5h" 18000 "$cost_display" "$cost_color")
[ -n "$rate" ] && parts+=("5h $rate")
rate=$(format_rate "$rate_7d" "$resets_7d" 604800)
[ -n "$rate" ] && parts+=("7d $rate")
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
