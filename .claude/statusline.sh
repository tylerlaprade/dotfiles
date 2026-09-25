#!/bin/bash
input=$(cat)
IFS=$'\037' read -r current_dir project_dir < <(printf '%s' "$input" | jq -r '[.workspace.current_dir, .workspace.project_dir // .workspace.current_dir] | join("\u001f")')
cd "$current_dir" 2>/dev/null || exit 0
settings_layers=()
for layer in "$HOME/.claude/settings.json" "$project_dir/.claude/settings.json" "$project_dir/.claude/settings.local.json"; do
  [ -f "$layer" ] && settings_layers+=("$layer")
done
settings=$(jq -n 'reduce inputs as $layer ({}; . + $layer)' "${settings_layers[@]}" </dev/null)

# Mirror Claude Code's auto-compact trigger from the raw input count so a routing mismatch can exceed 100%.
read -r used_tokens limit_tokens auto_compacts < <(
  echo "$input" | jq -r --argjson settings "$settings" '
    def positive_env($name): $ENV[$name] // "" | tonumber? // null | select(. != null and . > 0);
    def truthy_env($name): $ENV[$name] // "" | ascii_downcase | IN("1", "true", "yes", "on");
    20000 as $output_reserve_cap
    | 13000 as $summary_reserve
    | .context_window.context_window_size as $window
    | [
        .context_window.total_input_tokens,
        if truthy_env("DISABLE_COMPACT") or truthy_env("DISABLE_AUTO_COMPACT") or $settings.autoCompactEnabled == false then
          $window, false
        else
          ((positive_env("CLAUDE_CODE_AUTO_COMPACT_WINDOW") | [[., 1000000] | min, 100000] | max)
            // $settings.autoCompactWindow // $window) as $compact_window
          | (([$window, $compact_window] | min)
            - ([positive_env("CLAUDE_CODE_MAX_OUTPUT_TOKENS") // $output_reserve_cap, $output_reserve_cap] | min))
            as $effective_window
          | ($effective_window - $summary_reserve) as $reserved_limit
          | ((positive_env("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") | select(. <= 100)) // null) as $percent_override
          | if $percent_override then [($effective_window * $percent_override / 100 | floor), $reserved_limit] | min
            else $reserved_limit end,
          true
        end
      ] | @tsv'
)
pct=$(( used_tokens * 100 / limit_tokens ))
IFS=$'\037' read -r transcript_path rate_5h rate_7d resets_5h resets_7d model_name effort_level session_id raw_cost cost_cents < <(
  printf '%s' "$input" | jq -r '[
    .transcript_path // "",
    (.rate_limits.five_hour.used_percentage | if type == "number" then floor else "" end),
    (.rate_limits.seven_day.used_percentage | if type == "number" then floor else "" end),
    .rate_limits.five_hour.resets_at // "",
    .rate_limits.seven_day.resets_at // "",
    .model.display_name // "",
    .effort.level // "",
    .session_id // "",
    .cost.total_cost_usd // "",
    ((.cost.total_cost_usd // 0) * 100 | round)
  ] | join("\u001f")'
)

format_tokens() {
  local tokens=$1
  if [ "$tokens" -ge 1000000 ]; then
    local tenths=$(( tokens / 100000 ))
    if [ $((tenths % 10)) -eq 0 ]; then
      printf '%dm' $((tenths / 10))
    else
      printf '%d.%dm' $((tenths / 10)) $((tenths % 10))
    fi
  else
    printf '%dk' $(( tokens / 1000 ))
  fi
}

used_display=$(format_tokens "$used_tokens")
limit_display=$(format_tokens "$limit_tokens")

# Flag a compaction that fires away from the mirrored limit, since Claude Code can change its trigger.
# Claude Code checks before each request from the previous response's usage plus an estimate of what
# was added since, so one request can overshoot and compaction is then due. Only a request sent after a
# response already past the limit proves a missed compaction. The transcript is read backward so a long
# session costs only its last few responses.
drift_tolerance=$(( limit_tokens / 100 ))
drift_warning=""
compaction_due=false
if [ "$auto_compacts" = true ]; then
  read -r drift_kind drift_tokens < <(python3 - "$transcript_path" "$limit_tokens" "$drift_tolerance" 2>/dev/null <<'PYTHON'
import json
import os
import sys

transcript_path, limit, tolerance = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])


def lines_from_end(transcript):
    position = transcript.seek(0, os.SEEK_END)
    partial = b''
    while position > 0:
        chunk_size = min(position, 1 << 20)
        position -= chunk_size
        transcript.seek(position)
        lines = (transcript.read(chunk_size) + partial).split(b'\n')
        partial = lines.pop(0)
        yield from reversed(lines)
    yield partial


def total_tokens(message):
    usage = message['usage']
    return sum(usage.get(key) or 0 for key in
               ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'))


def past_limit(responses):
    return len(responses) == 2 and responses[1][1] > limit + tolerance


latest_responses_by_segment = [[]]
compaction = None
with open(transcript_path, 'rb') as transcript:
    for line in lines_from_end(transcript):
        responses = latest_responses_by_segment[-1]
        if past_limit(latest_responses_by_segment[0]) or compaction is not None and len(responses) == 2:
            break
        if b'"subtype":"compact_boundary"' in line:
            if compaction is not None:
                break
            compaction = json.loads(line)['compactMetadata']
            if compaction['trigger'] != 'auto':
                break
            latest_responses_by_segment.append([])
        elif b'"type":"assistant"' in line and len(responses) < 2:
            message = json.loads(line)['message']
            if message['model'] != '<synthetic>' and (not responses or responses[-1][0] != message['id']):
                responses.append((message['id'], total_tokens(message)))

if past_limit(latest_responses_by_segment[0]):
    print('missed')
elif compaction is not None and compaction['trigger'] == 'auto':
    if past_limit(latest_responses_by_segment[1]):
        print('late', compaction['preTokens'])
    elif compaction['preTokens'] < limit - tolerance:
        print('early', compaction['preTokens'])
PYTHON
  )
  case "$drift_kind" in
    missed) drift_warning="no compaction past limit" ;;
    late) drift_warning="compacted late at $(format_tokens "$drift_tokens")" ;;
    early) drift_warning="compacted early at $(format_tokens "$drift_tokens")" ;;
  esac
  [ "$drift_kind" != missed ] && [ "$used_tokens" -ge "$limit_tokens" ] && compaction_due=true
fi

RESET='\033[0m'
WHITE='\033[97m'
DIM='\033[90m'
ALERT='\033[91m'
YELLOW='\033[33m'

# Tomorrow Night gradient: (blue →) green → yellow → red with asymptotic red tail
# Sets global r, g, b. Args: value green_end yellow_point red_point [asymptotic_k] [blue_floor]
# When blue_floor is set, val ≤ blue_floor is pure blue and blue_floor→green_end blends blue→green.
tn_gradient() {
  local val=$1 green_end=$2 yellow_pt=$3 red_pt=$4 k=${5:-80} blue_floor=${6:-}
  if [ -n "$blue_floor" ] && [ "$val" -lt "$green_end" ]; then
    if [ "$val" -le "$blue_floor" ]; then
      r=129 g=162 b=190
    else
      local t=$(( (val - blue_floor) * 100 / (green_end - blue_floor) ))
      r=$(( 129 + (181 - 129) * t / 100 ))
      g=$(( 162 + (189 - 162) * t / 100 ))
      b=$(( 190 + (104 - 190) * t / 100 ))
    fi
  elif [ "$val" -le "$green_end" ]; then
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

rate_usage_gradient() {
  local val=$1
  if [ "$val" -le 55 ]; then
    r=181 g=189 b=104
  elif [ "$val" -le 75 ]; then
    local t=$(( (val - 55) * 100 / 20 ))
    r=$(( 181 + (240 - 181) * t / 100 ))
    g=$(( 189 + (198 - 189) * t / 100 ))
    b=$(( 104 + (116 - 104) * t / 100 ))
  elif [ "$val" -le 95 ]; then
    local t=$(( (val - 75) * 100 / 20 ))
    r=$(( 240 + (255 - 240) * t / 100 ))
    g=$(( 198 - 198 * t / 100 ))
    b=$(( 116 - 116 * t / 100 ))
  else
    r=255 g=0 b=0
  fi
}

# Time-of-day color for the clock (weekdays 4:30-6:45pm, every day 9:30pm-1am ET).
# White outside the windows; LERPs white→green→yellow→bright-red (nighttime inserts an
# extra white→blue pre-phase so the green-fade starts from blue). Bold kicks in at the
# yellow→red phase onward. Final phase of each window also toggles reverse video —
# on for seconds 0-29, off for 30-59. Statusline updates every 30s, so the toggle
# reads as a steady 30s-on/30s-off blink without per-session race.
format_time_color() {
  local t_str=$1 dow h m s secs phase_start t r g b bold="" reverse="" night=0 start_r=255 start_g=255 start_b=255
  read -r dow h m s < <(TZ="America/New_York" date "+%u %H %M %S")
  # 10# prefix prevents octal parsing on 08:xx / 09:xx
  secs=$((10#$h * 3600 + 10#$m * 60 + 10#$s))
  local P0 P1 P2 P3 P4 P_blue
  if [ "$dow" -le 5 ] && [ "$secs" -ge 59400 ] && [ "$secs" -lt 67500 ]; then
    # 4:30-6:45pm weekdays only: 15+15+15+90 min phases
    P0=59400 P1=60300 P2=61200 P3=62100 P4=67500
  elif [ "$secs" -ge 77400 ] || [ "$secs" -lt 3600 ]; then
    # 9:30pm-1am every day: 30+30+30+30+90 min phases (extra white→blue pre-phase)
    # Post-midnight: shift secs into the prior day's range so phase math keeps working.
    P0=77400 P_blue=79200 P1=81000 P2=82800 P3=84600 P4=90000
    night=1
    [ "$secs" -lt 3600 ] && secs=$(( secs + 86400 ))
  else
    printf '%b%s%b' "$WHITE" "$t_str" "$RESET"
    return
  fi
  if [ "$night" -eq 1 ] && [ "$secs" -lt "$P_blue" ]; then  # white -> blue (night pre-phase)
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / (P_blue - P0) ))
    r=$(( 255 + (50 - 255) * t / 100 ))
    g=$(( 255 + (130 - 255) * t / 100 ))
    b=255
    printf '\033[38;2;%d;%d;%dm%s\033[0m' "$r" "$g" "$b" "$t_str"
    return
  fi
  # After the pre-phase, the green-fade starts from blue instead of white on night windows.
  if [ "$night" -eq 1 ]; then
    start_r=50 start_g=130 start_b=255
    P0=$P_blue
  fi
  if [ "$secs" -lt "$P1" ]; then           # start_color -> green
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / (P1 - P0) ))
    r=$(( start_r + (0 - start_r) * t / 100 ))
    g=$(( start_g + (200 - start_g) * t / 100 ))
    b=$(( start_b + (0 - start_b) * t / 100 ))
  elif [ "$secs" -lt "$P2" ]; then         # green -> yellow
    phase_start=$P1
    t=$(( (secs - phase_start) * 100 / (P2 - P1) ))
    r=$(( 255 * t / 100 ))
    g=200
    b=0
  elif [ "$secs" -lt "$P3" ]; then         # yellow -> bright red + BOLD
    phase_start=$P2
    t=$(( (secs - phase_start) * 100 / (P3 - P2) ))
    r=255
    g=$(( 200 - 200 * t / 100 ))
    b=0
    bold='\033[1m'
  else                                     # bright red + BOLD + reverse toggle
    r=255; g=0; b=0
    bold='\033[1m'
    [ $((10#$s)) -lt 30 ] && reverse='\033[7m'
  fi
  printf '%b\033[38;2;%d;%d;%dm%b%s\033[0m' "$bold" "$r" "$g" "$b" "$reverse" "$t_str"
}

# Color tracks pressure against the auto-compact trigger.
# 0-55% stays green, 55-75% blends to yellow, 75-95% blends to red,
# and anything above 95% stays bright red.
rate_usage_gradient "$pct"
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
ctx_info="${bar}${bar_color} ${pct}% · ${used_display}/${limit_display}${RESET}"

# Persist 5h/7d so the claude() overage gate can read latest state.
# Per-session harness rate_limits is cached at last API response, so an idle
# session's render carries stale numbers. Skip the write unless our resets_5h
# is at least as new as the existing snapshot's — older reset = older data.
# Fable is not in this stdin payload; claude-usage.sh fetches that separately.
_snap_resets_5h=0
[ -f /tmp/claude-rate-limits.json ] && \
  _snap_resets_5h=$(jq -r '.resets_5h // 0' /tmp/claude-rate-limits.json 2>/dev/null)
if [ "${resets_5h:-0}" -ge "${_snap_resets_5h:-0}" ]; then
  printf '{"five_hour":%s,"seven_day":%s,"resets_5h":%s,"resets_7d":%s,"updated_at":%s}\n' \
    "${rate_5h:-0}" "${rate_7d:-0}" "${resets_5h:-0}" "${resets_7d:-0}" "$(date +%s)" \
    > /tmp/claude-rate-limits.json
fi

# Overage gate: kill all sessions if over threshold
if [ -f ~/.claude/overage-gate ] && [ ! -f /tmp/claude-overage-override ]; then
  _threshold=${CLAUDE_OVERAGE_THRESHOLD:-95}
  if [ "${rate_5h:-0}" -ge "$_threshold" ] || [ "${rate_7d:-0}" -ge "$_threshold" ]; then
    printf '%s 5h=%s%% 7d=%s%%\n' "$(date +%s)" "${rate_5h}" "${rate_7d}" >> /tmp/claude-overage-kills.log
    touch /tmp/claude-overage-killed
    pkill claude
  fi
fi

# Pace of what's left: remaining_time / remaining_budget, as a color in r/g/b.
# On schedule this is 1.0x, same as used/elapsed. An empty budget is T/0, and
# the red tail's limit at that unbounded ratio is (255,0,0).
# Args: $1 = used percent, $2 = resets (unix), $3 = window seconds.
pace_gradient() {
  local pct=$1 resets=$2 window_secs=$3
  local now=$(date +%s)
  local time_remaining=$(( resets - now ))
  [ "$time_remaining" -lt 0 ] && time_remaining=0
  [ "$time_remaining" -gt "$window_secs" ] && time_remaining=$window_secs

  if [ "$pct" -ge 100 ]; then
    r=255 g=0 b=0
    return
  fi

  # Project current burn rate to reset: how much will be used at reset if we
  # keep the current rate? 0% used → projected 0 (chill blue); 100% projected
  # → yellow (on pace); >125% → red (over pace). Clamp elapsed to 60s so a
  # fresh window with any usage projects large instead of undefined.
  local time_elapsed=$(( window_secs - time_remaining ))
  [ "$time_elapsed" -lt 60 ] && time_elapsed=60
  local projected=$(( pct * window_secs / time_elapsed ))
  [ "$projected" -gt 300 ] && projected=300

  tn_gradient "$projected" 75 100 125 80 50
}

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
    # Absolute hard-limit usage: 0-55% green, 55-75% green→yellow,
    # 75-95% yellow→bright red, 95%+ bright red. Print the number as
    # reported — no "+" for "maybe over."
    rate_usage_gradient "$pct"
    local pct_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    local suffix="%"
    # Stdin 5h/7d bars stop at 100, so "+" means "at or over." Fable from
    # /usage is an exact percent and does not pass this flag.
    [ -n "${6:-}" ] && [ "$pct" -ge 100 ] && suffix="%+"
    info="${pct_color}${pct}${suffix}${RESET}"
  fi

  if [ "$time_remaining" -gt 0 ]; then
    pace_gradient "$pct" "$resets" "$window_secs"
    local time_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    local reset_str
    local today=$(TZ="America/New_York" date +"%Y%j")
    local tomorrow=$(TZ="America/New_York" date -v+1d +"%Y%j")
    local reset_day=$(TZ="America/New_York" date -r "$resets" +"%Y%j" 2>/dev/null)
    if [ "$reset_day" = "$today" ]; then
      reset_str=$(TZ="America/New_York" date -r "$resets" +"%-I:%M %p" 2>/dev/null)
    elif [ "$reset_day" = "$tomorrow" ]; then
      reset_str="$(TZ="America/New_York" date -r "$resets" +"%-I:%M %p" 2>/dev/null)${RESET} tomorrow"
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
    # Reset-time proximity: blue just after reset → green → yellow → red as it nears.
    tn_gradient $(( time_elapsed * 100 / window_secs )) 55 80 95 80 20
    local reset_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    [ -n "$reset_str" ] && info="${info} (resets in ${time_color}${remaining}${RESET} at ${reset_color}${reset_str}${RESET})"
  fi

  echo "$info"
}

if [[ -n "$HIDE_GIT_PROMPT" ]]; then
  git_status=""
else
  git_status=$(git-status-line --async-pr)
fi
current_time=$(TZ="America/New_York" date +"%-I:%M %p")
model_name="${model_name% (1M context)}"

join_parts() {
  local joined=$1 part
  shift
  for part in "$@"; do
    joined+=" · $part"
  done
  printf '%s' "$joined"
}

# Line 1: model · context bar · compaction drift · session · time
parts=()
if [ -n "$model_name" ]; then
  model_part="${DIM}${model_name}"
  [ -n "$effort_level" ] && model_part="${model_part} ${effort_level}"
  parts+=("${model_part}${RESET}")
fi
parts+=("$ctx_info")
[ -n "$drift_warning" ] && parts+=("${ALERT}⚠ ${drift_warning}${RESET}")
[ "$compaction_due" = true ] && parts+=("${YELLOW}compaction due${RESET}")
[ -n "$session_id" ] && parts+=("${DIM}${session_id}${RESET}")
parts+=("$(format_time_color "$current_time")")
echo -e "$(join_parts "${parts[@]}")"

# Line 2: 5h · 7d · Fable
# claude-usage --async matches gh-pr-lookup: print cache, detach refresh.
rate_parts=()
_usage_cmd=$(command -v claude-usage 2>/dev/null || command -v claude-usage.sh 2>/dev/null || true)
_usage=""
fable_part=""
if [ -n "$_usage_cmd" ]; then
  # Pass stdin's 5h/7d percentages so claude-usage can skip refresh when
  # nothing has burned on this account since the last successful fetch.
  _usage=$(STATUSLINE_5H="${rate_5h:-0}" STATUSLINE_7D="${rate_7d:-0}" \
    "$_usage_cmd" --async 2>/dev/null) || true
fi
if [ -n "$_usage" ]; then
  IFS=$'\037' read -r _usage_ok rate_fable resets_fable usage_resets_7d _usage_error usage_5h usage_resets_5h usage_7d < <(
    printf '%s' "$_usage" | jq -r '[
      .ok != false,
      .fable // "",
      .resets_fable // "",
      .resets_7d // "",
      .error // "fetch failed",
      .five_hour // "",
      .resets_5h // "",
      .seven_day // ""
    ] | join("\u001f")'
  )
  if [ "$_usage_ok" != true ]; then
    if [ "$_usage_error" = "keychain unavailable" ]; then
      if [ -n "$rate_fable" ]; then
        fable_part="${DIM}Fable ${rate_fable}% · stale${RESET}"
      else
        fable_part="${DIM}Fable unavailable${RESET}"
      fi
    elif [ "$_usage_error" = "token expired" ] || [ "$_usage_error" = "no login" ] || [ "$_usage_error" = "no token" ] || [ "$_usage_error" = "HTTP 401" ]; then
      fable_part="${YELLOW}Fable: login required${RESET}"
    elif [ "$_usage_error" = "HTTP 429" ]; then
      if [ -n "$rate_fable" ]; then
        fable_part="${DIM}Fable ${rate_fable}% · rate limited${RESET}"
      else
        fable_part="${DIM}Fable: rate limited${RESET}"
      fi
    elif [ -n "$rate_fable" ]; then
      fable_part="${DIM}Fable ${rate_fable}% · fetch failed${RESET}"
    else
      fable_part="${DIM}Fable unavailable${RESET}"
    fi
  elif [ -n "$rate_fable" ]; then
    # Same weekly reset as 7d: omit the duplicate countdown, and pace-color the
    # percent so the pace still shows somewhere. Compare within the usage
    # payload, and allow a minute of slack: the API rounds the two windows
    # independently, so the same reset arrives as 14:59:59 and 15:00:00. A real
    # divergence would be hours, never seconds.
    if [ -n "$resets_fable" ] && [ -n "$usage_resets_7d" ] \
       && [ "$(( resets_fable > usage_resets_7d ? resets_fable - usage_resets_7d : usage_resets_7d - resets_fable ))" -le 60 ]; then
      pace_gradient "$rate_fable" "$resets_fable" 604800
      fable_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
      fable_part="Fable ${fable_color}${rate_fable}%${RESET}"
    else
      # Resets diverged: the countdown carries the pace, so the percent goes
      # back to the absolute gradient, same as 5h and 7d.
      rate=$(format_rate "$rate_fable" "$resets_fable" 604800)
      [ -n "$rate" ] && fable_part="Fable $rate"
    fi
  fi
elif [ -n "$_usage_cmd" ]; then
  fable_part="${DIM}Fable unavailable${RESET}"
fi
# Stdin rate_limits is empty until the first API response. The usage
# fetch already ran for Fable and includes 5h/7d; use those only while
# stdin has nothing.
if [ "${_usage_ok:-}" = true ]; then
  if [ -z "$rate_5h" ]; then
    rate_5h=$usage_5h
    resets_5h=$usage_resets_5h
  fi
  if [ -z "$rate_7d" ]; then
    rate_7d=$usage_7d
    resets_7d=$usage_resets_7d
  fi
fi
cost_display="" cost_color=""
if [ "${rate_5h:-0}" -ge 100 ]; then
  if [ -n "$raw_cost" ]; then
    session_cost=$(printf "%.2f" "$raw_cost")
    # Asymptotic red from 100%-red base: (204,102,102) → (255,0,0)
    t=$(( cost_cents * 100 / (cost_cents + 2000) ))
    r=$(( 204 + (255 - 204) * t / 100 ))
    g=$(( 102 - 102 * t / 100 ))
    b=$(( 102 - 102 * t / 100 ))
    cost_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
    cost_display="\$${session_cost}"
  fi
fi
rate=$(format_rate "$rate_5h" "$resets_5h" 18000 "$cost_display" "$cost_color" plus)
[ -n "$rate" ] && rate_parts+=("5h $rate")
rate=$(format_rate "$rate_7d" "$resets_7d" 604800 "" "" plus)
[ -n "$rate" ] && rate_parts+=("7d $rate")
[ -n "$fable_part" ] && rate_parts+=("$fable_part")
if (( ${#rate_parts[@]} )); then
  echo -e "${DIM}Usage${RESET} · $(join_parts "${rate_parts[@]}")"
fi

# Git info
if [ -n "$git_status" ]; then
  echo -e "$git_status"
fi
