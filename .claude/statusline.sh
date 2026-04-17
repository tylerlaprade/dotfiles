#!/bin/bash
[[ -n "$HIDE_GIT_PROMPT" ]] && exit 0
input=$(cat)
cd "$(echo "$input" | jq -r '.workspace.current_dir')" 2>/dev/null || exit 0

# .used_percentage is clamped to 0-100 by CC, so it can't show overflow.
# Compute % from raw tokens in .current_usage divided by .context_window_size
# (so 100% = 200k in 200k mode, 100% = 1M in 1M mode).
pct=$(echo "$input" | jq -r '.context_window | ((.current_usage // {}) as $u | (($u.input_tokens // 0) + ($u.cache_creation_input_tokens // 0) + ($u.cache_read_input_tokens // 0)) * 100 / (.context_window_size // 200000)) | round')

RESET='\033[0m'
WHITE='\033[97m'
DIM='\033[90m'

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

# Time-of-day color for the clock (weekdays 4:30-6:00pm ET).
# White outside the window; LERPs through the context-bar palette
# (matches the green/yellow anchors at lines 43-47 and the bright-red
# endpoint at line 31). Bold from 5:15pm onward. 5:30-6:00pm also
# toggles reverse video on each render via a state file — alternation
# is tied to statusline refresh cadence, not wall clock.
format_time_color() {
  local t_str=$1 dow h m s secs phase_start t r g b bold="" reverse=""
  dow=$(TZ="America/New_York" date +%u)
  if [ "$dow" -ge 6 ]; then
    printf '%b%s%b' "$WHITE" "$t_str" "$RESET"
    return
  fi
  read -r h m s < <(TZ="America/New_York" date "+%H %M %S")
  # 10# prefix prevents octal parsing on 08:xx / 09:xx
  secs=$((10#$h * 3600 + 10#$m * 60 + 10#$s))
  local P0=59400 P1=60300 P2=61200 P3=62100 P4=63000 P5=64800
  if [ "$secs" -lt "$P0" ] || [ "$secs" -ge "$P5" ]; then
    printf '%b%s%b' "$WHITE" "$t_str" "$RESET"
    return
  fi
  if [ "$secs" -lt "$P1" ]; then           # 4:30-4:45 white -> green
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / 900 ))
    r=$(( 255 + (0 - 255) * t / 100 ))
    g=$(( 255 + (200 - 255) * t / 100 ))
    b=$(( 255 + (0 - 255) * t / 100 ))
  elif [ "$secs" -lt "$P2" ]; then         # 4:45-5:00 green -> yellow
    phase_start=$P1
    t=$(( (secs - phase_start) * 100 / 900 ))
    r=$(( 255 * t / 100 ))
    g=200
    b=0
  elif [ "$secs" -lt "$P3" ]; then         # 5:00-5:15 yellow -> dark red
    phase_start=$P2
    t=$(( (secs - phase_start) * 100 / 900 ))
    r=$(( 255 + (160 - 255) * t / 100 ))
    g=$(( 200 - 200 * t / 100 ))
    b=0
  elif [ "$secs" -lt "$P4" ]; then         # 5:15-5:30 dark red -> bright red + BOLD
    phase_start=$P3
    t=$(( (secs - phase_start) * 100 / 900 ))
    r=$(( 160 + (255 - 160) * t / 100 ))
    g=0
    b=0
    bold='\033[1m'
  else                                     # 5:30-6:00 bright red + BOLD + reverse toggle
    r=255; g=0; b=0
    bold='\033[1m'
    local flash_state="/tmp/claude-statusline-flash.$USER"
    if [ -f "$flash_state" ]; then
      rm -f "$flash_state"
      reverse='\033[7m'
    else
      touch "$flash_state"
    fi
  fi
  printf '%b\033[38;2;%d;%d;%dm%b%s\033[0m' "$bold" "$r" "$g" "$b" "$reverse" "$t_str"
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
  r=$(( 255 - (255 - 160) * t / 100 ))
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

# Overage gate: write rate limits + kill all sessions if over threshold
if [ -f ~/.claude/overage-gate ]; then
  _rl_tmp="/tmp/claude-rate-limits.$$.tmp"
  _rl_file="/tmp/claude-rate-limits.json"
  printf '{"five_hour":%s,"seven_day":%s,"resets_5h":%s,"resets_7d":%s,"updated_at":%s}\n' \
    "${rate_5h:-0}" "${rate_7d:-0}" "${resets_5h:-0}" "${resets_7d:-0}" "$(date +%s)" \
    > "$_rl_tmp" && mv "$_rl_tmp" "$_rl_file" 2>/dev/null
  if [ ! -f /tmp/claude-overage-override ]; then
    _threshold=${CLAUDE_OVERAGE_THRESHOLD:-95}
    if [ "${rate_5h:-0}" -ge "$_threshold" ] || [ "${rate_7d:-0}" -ge "$_threshold" ]; then
      printf '%s 5h=%s%% 7d=%s%%\n' "$(date +%s)" "${rate_5h}" "${rate_7d}" >> /tmp/claude-overage-kills.log
      touch /tmp/claude-overage-killed
      pkill claude
    fi
  fi
fi

# For pace coloring, trim a natural window end back to the most recent weekday 5pm ET
# if `end` falls in the following off-hours dead zone (Mon-Thu 5pm → next 9am, Fri 5pm → Mon 9am).
# Else return `end` unchanged. Args: $1 = now (unix), $2 = end (unix).
work_trimmed_end() {
  local now=$1 end=$2 ymd cutoff dow fwd next_ymd next_work
  ymd=$(TZ="America/New_York" date -r "$end" +%Y-%m-%d)
  cutoff=$(TZ="America/New_York" date -j -f "%Y-%m-%d %H:%M:%S" "$ymd 17:00:00" +%s 2>/dev/null)
  [ "$cutoff" -gt "$end" ] && cutoff=$(( cutoff - 86400 ))
  dow=$(TZ="America/New_York" date -r "$cutoff" +%u)
  case "$dow" in
    6) cutoff=$(( cutoff - 86400 )); dow=5 ;;
    7) cutoff=$(( cutoff - 2 * 86400 )); dow=5 ;;
  esac
  [ "$cutoff" -lt "$now" ] && { echo "$end"; return; }
  [ "$dow" -eq 5 ] && fwd=3 || fwd=1
  next_ymd=$(TZ="America/New_York" date -r "$(( cutoff + fwd * 86400 ))" +%Y-%m-%d)
  next_work=$(TZ="America/New_York" date -j -f "%Y-%m-%d %H:%M:%S" "$next_ymd 09:00:00" +%s 2>/dev/null)
  [ "$end" -lt "$next_work" ] && echo "$cutoff" || echo "$end"
}

format_rate() {
  local pct=$1 resets=$2 window_secs=$3 display_override=$4 display_color=$5
  [ -z "$pct" ] && return

  local now=$(date +%s)
  local time_remaining=$(( resets - now ))
  [ "$time_remaining" -lt 0 ] && time_remaining=0
  local time_elapsed=$(( window_secs - time_remaining ))
  [ "$time_elapsed" -lt 60 ] && time_elapsed=60
  # Trim to last weekday 5pm ET for pace coloring only (display stays untouched)
  local effective_end=$(work_trimmed_end "$now" "$resets")
  local effective_window_secs=$(( window_secs - (resets - effective_end) ))
  [ "$effective_window_secs" -lt 60 ] && effective_window_secs=60

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
  local time_elapsed_pct=$(( time_elapsed * 100 / effective_window_secs ))
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
model_name=$(echo "$input" | jq -r '.model.display_name // empty')

# Line 1: model · context bar · rates · time
parts=()
[ -n "$model_name" ] && parts+=("${DIM}${model_name}${RESET}")
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
parts+=("$(format_time_color "$current_time")")

echo -e "$(printf '%s' "${parts[0]}")$(printf ' · %s' "${parts[@]:1}")"

# Line 2: git info
echo -e "$git_status"

# Keep Ghostty tab title current (zsh hooks don't fire during TUI apps)
# Only override title when there's a PR; otherwise let Claude Code's own title persist
if [[ "$git_status" =~ (#[0-9]+\ .*) ]]; then
  printf '\e]0;%s\a' "${BASH_REMATCH[1]}" > /dev/tty 2>/dev/null
fi
