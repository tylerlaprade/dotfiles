#!/bin/bash
input=$(cat)
workspace_dirs=$(jq -r '[.workspace.current_dir, .workspace.project_dir // .workspace.current_dir] | join("\u001f")' <<< "$input")
IFS=$'\037' read -r current_dir project_dir <<< "$workspace_dirs"
cd "$current_dir" 2>/dev/null || exit 0
settings_layers=()
for layer in "$HOME/.claude/settings.json" "$project_dir/.claude/settings.json" "$project_dir/.claude/settings.local.json"; do
  [[ -f "$layer" ]] && settings_layers+=("$layer")
done
settings=$(jq -n 'reduce inputs as $layer ({}; . + $layer)' "${settings_layers[@]}" </dev/null)

# Mirror Claude Code's auto-compact trigger from the raw input count so a routing mismatch can exceed 100%.
compaction_fields=$(
  jq -r --argjson settings "$settings" '
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
      ] | @tsv' <<< "$input"
)
read -r used_tokens limit_tokens auto_compacts <<< "$compaction_fields"
pct=$(( used_tokens * 100 / limit_tokens ))
input_fields=$(
  jq -r '[
    (.rate_limits.five_hour.used_percentage | if type == "number" then floor else "" end),
    (.rate_limits.seven_day.used_percentage | if type == "number" then floor else "" end),
    .rate_limits.five_hour.resets_at // "",
    .rate_limits.seven_day.resets_at // "",
    .model.display_name // "",
    .effort.level // "",
    .session_id // "",
    .cost.total_cost_usd // "",
    ((.cost.total_cost_usd // 0) * 100 | round)
  ] | join("\u001f")' <<< "$input"
)
IFS=$'\037' read -r rate_5h rate_7d resets_5h resets_7d model_name effort_level session_id raw_cost cost_cents <<< "$input_fields"
clock_fields=$(date '+%s %u %H %M %S %-I:%M %p')
read -r now clock_weekday clock_hour clock_minute clock_second current_time <<< "$clock_fields"

format_tokens() {
  local tokens=$1
  if [[ "$tokens" -ge 1000000 ]]; then
    local tenths=$(( tokens / 100000 ))
    if [[ $((tenths % 10)) -eq 0 ]]; then
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

# Claude Code decides compaction before each request, so a context past the limit compacts on the next one.
compaction_due=false
[[ "$auto_compacts" = true ]] && [[ "$used_tokens" -ge "$limit_tokens" ]] && compaction_due=true

RESET='\033[0m'
DIM='\033[90m'

# Tomorrow Night, which also stands in for any terminal whose theme is unreadable: foreground, blue,
# green, yellow, red, the alarm red past red, and background, as r g b each.
fallback_palette=(255 255 255 129 162 190 181 189 104 240 198 116 204 102 102 255 0 0 40 44 52)

# macOS rewrites this plist when it switches appearance, so the plist keys the saved palette.
system_appearance() {
  local style
  palette_sources+=("$HOME/Library/Preferences/.GlobalPreferences.plist")
  style=$(defaults read -g AppleInterfaceStyle 2>/dev/null)
  if [[ "$style" = Dark ]]; then
    appearance=dark
  else
    appearance=light
  fi
}

# Each resolver sets theme_colors to the foreground, blue, green, yellow, red, and background as hex.
resolve_rex_palette() {
  local appearance theme_id app themes colors
  palette_sources+=("$HOME/Library/Preferences/com.superlogical.rex.plist")
  appearance=$(defaults read com.superlogical.rex Workspace.appearance 2>/dev/null)
  case "$appearance" in
    light|dark) ;;
    auto|'') system_appearance ;;
    *) return 1 ;;
  esac
  theme_id=$(defaults read com.superlogical.rex "Workspace.${appearance}ThemeID" 2>/dev/null) || return
  app=$(lsappinfo info -only bundlepath com.superlogical.rex 2>/dev/null)
  if [[ "$app" != *'"LSBundlePath"="'* ]]; then
    palette_cacheable=false
    return 1
  fi
  app=${app#*'"LSBundlePath"="'}
  themes=${app%'"'}/Contents/Resources/SuperUI_SuperDesignTokens.bundle/Contents/Resources/themes.json
  palette_sources+=("$themes")
  colors=$(jq -r --arg id "$theme_id" '
    first(.[] | select(.id == $id) | .source | [.foreground, .ansi[4], .ansi[2], .ansi[3], .ansi[1], .background])
    | select(all(.[]; test("^#?[0-9A-Fa-f]{6}$"))) | join(" ")' "$themes" 2>/dev/null)
  [[ -n "$colors" ]] || return
  read -r -a theme_colors <<< "$colors"
}

# Sets parsed_theme and parsed_colors (palette indexes, then 256 background and 257 foreground) from one Ghostty file.
parse_ghostty_file() {
  local line setting='^[[:space:]]*(theme|background|foreground|palette)[[:space:]]*=[[:space:]]*"?([^"]*[^"[:space:]])"?[[:space:]]*$'
  local hex='^#?([0-9A-Fa-f]{6})$' palette_entry='^([0-9]+)[[:space:]]*=[[:space:]]*#?([0-9A-Fa-f]{6})$'
  parsed_theme='' parsed_colors=()
  [[ -f "$1" ]] || return
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ $setting ]] || continue
    local value=${BASH_REMATCH[2]}
    case "${BASH_REMATCH[1]}" in
      theme) parsed_theme=$value ;;
      background) [[ "$value" =~ $hex ]] && parsed_colors[256]=${BASH_REMATCH[1]} ;;
      foreground) [[ "$value" =~ $hex ]] && parsed_colors[257]=${BASH_REMATCH[1]} ;;
      palette) [[ "$value" =~ $palette_entry ]] && parsed_colors[BASH_REMATCH[1]]=${BASH_REMATCH[2]} ;;
      *) printf 'statusline: no case for Ghostty setting %s\n' "${BASH_REMATCH[1]}" >&2; exit 1 ;;
    esac
  done < "$1"
}

resolve_ghostty_palette() {
  local config_dir=${XDG_CONFIG_HOME:-$HOME/.config}/ghostty appearance config theme='' entry index theme_file hex offset=0
  local paired_theme='^[[:space:]]*(light|dark):[[:space:]]*(.*[^[:space:]])'
  local -a colors=() config_colors=() entries=() theme_files=()
  for index in 257 4 2 3 1; do
    printf -v hex '%02x%02x%02x' "${fallback_palette[@]:offset:3}"
    colors[index]=$hex
    (( offset += 3 ))
  done
  printf -v hex '%02x%02x%02x' "${fallback_palette[@]:18:3}"
  colors[256]=$hex
  for config in "$config_dir/config" "$HOME/Library/Application Support/com.mitchellh.ghostty/config"; do
    palette_sources+=("$config")
    parse_ghostty_file "$config"
    [[ -n "$parsed_theme" ]] && theme=$parsed_theme
    for index in "${!parsed_colors[@]}"; do
      config_colors[index]=${parsed_colors[index]}
    done
  done
  if [[ "$theme" == *light:* || "$theme" == *dark:* ]]; then
    system_appearance
    IFS=, read -r -a entries <<< "$theme"
    for entry in "${entries[@]}"; do
      [[ "$entry" =~ $paired_theme ]] && [[ "${BASH_REMATCH[1]}" = "$appearance" ]] && theme=${BASH_REMATCH[2]}
    done
  fi
  if [[ "$theme" == /* ]]; then
    theme_files=("$theme")
  elif [[ -n "$theme" ]]; then
    theme_files=("$config_dir/themes/$theme")
    [[ -n "${GHOSTTY_RESOURCES_DIR:-}" ]] && theme_files+=("$GHOSTTY_RESOURCES_DIR/themes/$theme")
  fi
  for theme_file in "${theme_files[@]}"; do
    palette_sources+=("$theme_file")
    [[ -f "$theme_file" ]] || continue
    parse_ghostty_file "$theme_file"
    for index in "${!parsed_colors[@]}"; do
      colors[index]=${parsed_colors[index]}
    done
    break
  done
  for index in "${!config_colors[@]}"; do
    colors[index]=${config_colors[index]}
  done
  theme_colors=("${colors[257]}" "${colors[4]}" "${colors[2]}" "${colors[3]}" "${colors[1]}" "${colors[256]}")
}

# Reuses the palette saved for this terminal until a file it came from changes, appears, or disappears.
load_palette() {
  local resolver=$1 source hex joined_present joined_absent palette_cache=${TMPDIR:-/tmp}
  local -a present=() absent=()
  palette_cache=${palette_cache%/}/statusline-palette-${TERM_PROGRAM:-}
  palette=()
  if [[ -f "$palette_cache" ]]; then
    { IFS=$'\037' read -r -a present; IFS=$'\037' read -r -a absent; read -r -a palette; } < "$palette_cache"
    for source in "${present[@]}"; do
      [[ -e "$source" && "$palette_cache" -nt "$source" ]] || palette=()
    done
    for source in "${absent[@]}"; do
      [[ -e "$source" ]] && palette=()
    done
  fi
  (( ${#palette[@]} )) && return
  palette_sources=("${BASH_SOURCE[0]}") theme_colors=() palette_cacheable=true
  if "$resolver"; then
    for hex in "${theme_colors[@]}"; do
      hex=${hex#\#}
      palette+=("$(( 16#${hex:0:2} ))" "$(( 16#${hex:2:2} ))" "$(( 16#${hex:4:2} ))")
    done
    palette=("${palette[@]:0:15}" 255 0 0 "${palette[@]:15:3}")
  else
    palette=("${fallback_palette[@]}")
  fi
  [[ "$palette_cacheable" = true ]] || return
  present=() absent=()
  for source in "${palette_sources[@]}"; do
    if [[ -e "$source" ]]; then present+=("$source"); else absent+=("$source"); fi
  done
  printf -v joined_present '%s\037' "${present[@]}"
  printf -v joined_absent '%s\037' "${absent[@]}"
  printf '%s\n%s\n%s\n' "${joined_present%$'\037'}" "${joined_absent%$'\037'}" "${palette[*]}" > "$palette_cache.$$" \
    && mv -f "$palette_cache.$$" "$palette_cache"
}

case "${TERM_PROGRAM:-}" in
  rex) load_palette resolve_rex_palette ;;
  ghostty) load_palette resolve_ghostty_palette ;;
  *) palette=("${fallback_palette[@]}") ;;
esac
foreground_rgb=("${palette[@]:0:3}") blue_rgb=("${palette[@]:3:3}") green_rgb=("${palette[@]:6:3}")
yellow_rgb=("${palette[@]:9:3}") red_rgb=("${palette[@]:12:3}") alarm_rgb=("${palette[@]:15:3}")
background_rgb=("${palette[@]:18:3}")
printf -v FOREGROUND '\033[38;2;%d;%d;%dm' "${foreground_rgb[@]}"
printf -v YELLOW '\033[38;2;%d;%d;%dm' "${yellow_rgb[@]}"

set_color() {
  r=$1 g=$2 b=$3
}

# Sets r, g, b to the color a percent of the way from the first r g b to the second.
blend() {
  r=$(( $1 + ($4 - $1) * $7 / 100 ))
  g=$(( $2 + ($5 - $2) * $7 / 100 ))
  b=$(( $3 + ($6 - $3) * $7 / 100 ))
}

# Theme gradient: (blue →) green → yellow → red with an asymptotic tail toward the alarm red
# Sets global r, g, b. Args: value green_end yellow_point red_point [asymptotic_k] [blue_floor]
# When blue_floor is set, val ≤ blue_floor is pure blue and blue_floor→green_end blends blue→green.
severity_gradient() {
  local val=$1 green_end=$2 yellow_pt=$3 red_pt=$4 k=${5:-80} blue_floor=${6:-}
  if [[ -n "$blue_floor" ]] && [[ "$val" -lt "$green_end" ]]; then
    if [[ "$val" -le "$blue_floor" ]]; then
      set_color "${blue_rgb[@]}"
    else
      blend "${blue_rgb[@]}" "${green_rgb[@]}" $(( (val - blue_floor) * 100 / (green_end - blue_floor) ))
    fi
  elif [[ "$val" -le "$green_end" ]]; then
    set_color "${green_rgb[@]}"
  elif [[ "$val" -le "$yellow_pt" ]]; then
    blend "${green_rgb[@]}" "${yellow_rgb[@]}" $(( (val - green_end) * 100 / (yellow_pt - green_end) ))
  elif [[ "$val" -le "$red_pt" ]]; then
    blend "${yellow_rgb[@]}" "${red_rgb[@]}" $(( (val - yellow_pt) * 100 / (red_pt - yellow_pt) ))
  else
    blend "${red_rgb[@]}" "${alarm_rgb[@]}" $(( (val - red_pt) * 100 / (val - red_pt + k) ))
  fi
}

rate_usage_gradient() {
  local val=$1
  if [[ "$val" -le 55 ]]; then
    set_color "${green_rgb[@]}"
  elif [[ "$val" -le 75 ]]; then
    blend "${green_rgb[@]}" "${yellow_rgb[@]}" $(( (val - 55) * 100 / 20 ))
  elif [[ "$val" -le 95 ]]; then
    blend "${yellow_rgb[@]}" "${alarm_rgb[@]}" $(( (val - 75) * 100 / 20 ))
  else
    set_color "${alarm_rgb[@]}"
  fi
}

# Sets battery to a 10-cell bar in the color the caller left in r, g, b, with a smooth transition
# cell, and battery_color to that color. An optional cell index carries a pace mark in the theme's
# foreground, drawn over that cell's shade so the bar stays whole. Sets variables instead of printing,
# so a render starts no subshell.
build_battery() {
  local pct=$1 mark_cell=${2:--1} filled=$(( $1 * 10 / 100 )) transition=░ transition_percent=25 cell shade percent mark
  [[ "$filled" -gt 10 ]] && filled=10
  if [[ $(( pct * 10 % 100 )) -ge 50 ]]; then
    transition=▒ transition_percent=50
  fi
  printf -v battery_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
  battery=$battery_color
  for (( cell = 0; cell < 10; cell++ )); do
    # Rex and Ghostty draw ▓, ▒ and ░ as 75%, 50% and 25% of the color over the background.
    if [[ "$cell" -lt "$filled" ]]; then
      shade=▓ percent=75
    elif [[ "$cell" -eq "$filled" ]]; then
      shade=$transition percent=$transition_percent
    else
      shade=░ percent=25
    fi
    if [[ "$cell" -eq "$mark_cell" ]]; then
      printf -v mark '\033[48;2;%d;%d;%dm\033[1;38;2;%d;%d;%dm│\033[0m' \
        $(( background_rgb[0] + (r - background_rgb[0]) * percent / 100 )) \
        $(( background_rgb[1] + (g - background_rgb[1]) * percent / 100 )) \
        $(( background_rgb[2] + (b - background_rgb[2]) * percent / 100 )) "${foreground_rgb[@]}"
      battery+=$mark$battery_color
    else
      battery+=$shade
    fi
  done
}

# Time-of-day color for the clock (weekdays 4:30-6:45pm, every day 9:30pm-1am local).
# Foreground outside the windows; LERPs foreground→green→yellow→alarm red (nighttime inserts an
# extra foreground→blue pre-phase so the green-fade starts from blue). Bold kicks in at the
# yellow→red phase onward. Final phase of each window also toggles reverse video —
# on for seconds 0-29, off for 30-59. Statusline updates every 30s, so the toggle
# reads as a steady 30s-on/30s-off blink without per-session race.
format_time_color() {
  local t_str=$1 dow=$clock_weekday h=$clock_hour m=$clock_minute s=$clock_second secs phase_start t r g b bold="" reverse=""
  local night=0 start_rgb=("${foreground_rgb[@]}")
  # 10# prefix prevents octal parsing on 08:xx / 09:xx
  secs=$((10#$h * 3600 + 10#$m * 60 + 10#$s))
  local P0 P1 P2 P3 P_blue
  if [[ "$dow" -le 5 ]] && [[ "$secs" -ge 59400 ]] && [[ "$secs" -lt 67500 ]]; then
    # 4:30-6:45pm weekdays only: 15+15+15+90 min phases
    P0=59400 P1=60300 P2=61200 P3=62100
  elif [[ "$secs" -ge 77400 ]] || [[ "$secs" -lt 3600 ]]; then
    # 9:30pm-1am every day: 30+30+30+30+90 min phases (extra white→blue pre-phase)
    # Post-midnight: shift secs into the prior day's range so phase math keeps working.
    P0=77400 P_blue=79200 P1=81000 P2=82800 P3=84600
    night=1
    [[ "$secs" -lt 3600 ]] && secs=$(( secs + 86400 ))
  else
    printf '%b%s%b' "$FOREGROUND" "$t_str" "$RESET"
    return
  fi
  if [[ "$night" -eq 1 ]] && [[ "$secs" -lt "$P_blue" ]]; then  # foreground -> blue (night pre-phase)
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / (P_blue - P0) ))
    blend "${foreground_rgb[@]}" "${blue_rgb[@]}" "$t"
    printf '\033[38;2;%d;%d;%dm%s\033[0m' "$r" "$g" "$b" "$t_str"
    return
  fi
  # After the pre-phase, the green-fade starts from blue instead of the foreground on night windows.
  if [[ "$night" -eq 1 ]]; then
    start_rgb=("${blue_rgb[@]}")
    P0=$P_blue
  fi
  if [[ "$secs" -lt "$P1" ]]; then           # start_color -> green
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / (P1 - P0) ))
    blend "${start_rgb[@]}" "${green_rgb[@]}" "$t"
  elif [[ "$secs" -lt "$P2" ]]; then         # green -> yellow
    phase_start=$P1
    t=$(( (secs - phase_start) * 100 / (P2 - P1) ))
    blend "${green_rgb[@]}" "${yellow_rgb[@]}" "$t"
  elif [[ "$secs" -lt "$P3" ]]; then         # yellow -> alarm red + BOLD
    phase_start=$P2
    t=$(( (secs - phase_start) * 100 / (P3 - P2) ))
    blend "${yellow_rgb[@]}" "${alarm_rgb[@]}" "$t"
    bold='\033[1m'
  else                                     # alarm red + BOLD + reverse toggle
    set_color "${alarm_rgb[@]}"
    bold='\033[1m'
    [[ $((10#$s)) -lt 30 ]] && reverse='\033[7m'
  fi
  printf '%b\033[38;2;%d;%d;%dm%b%s\033[0m' "$bold" "$r" "$g" "$b" "$reverse" "$t_str"
}

# Color tracks pressure against the auto-compact trigger.
# 0-55% stays green, 55-75% blends to yellow, 75-95% blends to red,
# and anything above 95% stays bright red.
rate_usage_gradient "$pct"
build_battery "$pct"
bar=$battery
bar_color=$battery_color
ctx_info="${bar}${bar_color} ${pct}% · ${used_display}/${limit_display}${RESET}"

# Persist 5h/7d so the claude() overage gate can read latest state.
# Per-session harness rate_limits is cached at last API response, so an idle
# session's render carries stale numbers. Skip the write unless our resets_5h
# is at least as new as the existing snapshot's — older reset = older data.
# Fable is not in this stdin payload; claude-usage.sh fetches that separately.
_snap_resets_5h=0
[[ -f /tmp/claude-rate-limits.json ]] && \
  _snap_resets_5h=$(jq -r '.resets_5h // 0' /tmp/claude-rate-limits.json 2>/dev/null)
if [[ "${resets_5h:-0}" -ge "${_snap_resets_5h:-0}" ]]; then
  printf '{"five_hour":%s,"seven_day":%s,"resets_5h":%s,"resets_7d":%s,"updated_at":%s}\n' \
    "${rate_5h:-0}" "${rate_7d:-0}" "${resets_5h:-0}" "${resets_7d:-0}" "$now" \
    > /tmp/claude-rate-limits.json
fi

# Overage gate: kill all sessions if over threshold
if [[ -f ~/.claude/overage-gate ]] && [[ ! -f /tmp/claude-overage-override ]]; then
  _threshold=${CLAUDE_OVERAGE_THRESHOLD:-95}
  if [[ "${rate_5h:-0}" -ge "$_threshold" ]] || [[ "${rate_7d:-0}" -ge "$_threshold" ]]; then
    printf '%s 5h=%s%% 7d=%s%%\n' "$now" "${rate_5h}" "${rate_7d}" >> /tmp/claude-overage-kills.log
    touch /tmp/claude-overage-killed
    pkill claude
  fi
fi

# Pace of what's left: remaining_time / remaining_budget, as a color in r/g/b.
# On schedule this is 1.0x, same as used/elapsed. An empty budget is T/0, and
# the red tail's limit at that unbounded ratio is the alarm red.
# Args: $1 = used percent, $2 = resets (unix), $3 = window seconds.
pace_gradient() {
  local pct=$1 resets=$2 window_secs=$3
  local time_remaining=$(( resets - now ))
  [[ "$time_remaining" -lt 0 ]] && time_remaining=0
  [[ "$time_remaining" -gt "$window_secs" ]] && time_remaining=$window_secs

  if [[ "$pct" -ge 100 ]]; then
    set_color "${alarm_rgb[@]}"
    return
  fi

  # Project current burn rate to reset: how much will be used at reset if we
  # keep the current rate? 0% used → projected 0 (chill blue); 100% projected
  # → yellow (on pace); >125% → red (over pace). Clamp elapsed to 60s so a
  # fresh window with any usage projects large instead of undefined.
  local time_elapsed=$(( window_secs - time_remaining ))
  [[ "$time_elapsed" -lt 60 ]] && time_elapsed=60
  local projected=$(( pct * window_secs / time_elapsed ))
  [[ "$projected" -gt 300 ]] && projected=300

  severity_gradient "$projected" 75 100 125 80 50
}

format_rate() {
  local pct=$1 resets=$2 window_secs=$3 display_override=$4 display_color=$5
  [[ -z "$pct" ]] && return

  local info
  if [[ -n "$display_override" ]]; then
    info="${display_color}${display_override}${RESET}"
  else
    pace_gradient "$pct" "$resets" "$window_secs"
    local pct_color
    printf -v pct_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
    local suffix="%"
    # Stdin 5h/7d bars stop at 100, so "+" means "at or over." Fable from
    # /usage is an exact percent and does not pass this flag.
    [[ -n "${6:-}" ]] && [[ "$pct" -ge 100 ]] && suffix="%+"
    info="${pct_color}${pct}${suffix}${RESET}"
  fi

  format_countdown "$resets" "$window_secs"
  [[ -n "$countdown" ]] && info+=" $countdown"
  echo "$info"
}

# Sets countdown to "(resets in ...)" colored by how soon the reset is: blue just after
# one, then green, yellow, and red as the next nears. Empty once the reset has passed.
format_countdown() {
  local window_secs=$2 time_remaining=$(( $1 - now )) time_elapsed remaining
  countdown=''
  [[ "$time_remaining" -gt 0 ]] || return 0
  time_elapsed=$(( window_secs - time_remaining ))
  [[ "$time_elapsed" -lt 60 ]] && time_elapsed=60
  if [[ "$time_remaining" -ge 86400 ]]; then
    remaining="$(( time_remaining / 86400 ))d $(( time_remaining % 86400 / 3600 ))h"
  elif [[ "$time_remaining" -ge 3600 ]]; then
    remaining="$(( time_remaining / 3600 ))h $(( time_remaining % 3600 / 60 ))m"
  else
    remaining="$(( time_remaining / 60 ))m"
  fi
  severity_gradient $(( time_elapsed * 100 / window_secs )) 55 80 95 80 20
  printf -v countdown '(resets in \033[38;2;%d;%d;%dm%s%b)' "$r" "$g" "$b" "$remaining" "$RESET"
}

# Sets weekly_part to a label, a battery in the pace color with a mark where an even
# burn would put the week by now, and the percent. A fourth argument marks 100 as "at or over."
weekly_battery() {
  local label=$1 pct=$2 resets=$3 week_left=$(( $3 - now )) pace_cell=-1 percent
  if [[ "$week_left" -gt 0 ]]; then
    [[ "$week_left" -gt 604800 ]] && week_left=604800
    pace_cell=$(( (604800 - week_left) * 10 / 604800 ))
  fi
  pace_gradient "$pct" "$resets" 604800
  build_battery "$pct" "$pace_cell"
  percent="${pct}%"
  [[ -n "${4:-}" ]] && [[ "$pct" -ge 100 ]] && percent+="+"
  weekly_part="$label ${battery}${RESET} ${battery_color}${percent}${RESET}"
}

if [[ -n "$HIDE_GIT_PROMPT" ]]; then
  git_status=""
else
  git_status=$(git-status-line --async-pr)
fi
model_name="${model_name% (1M context)}"

join_parts() {
  local joined=$1 part
  shift
  for part in "$@"; do
    joined+=" · $part"
  done
  printf '%s' "$joined"
}

# Line 1: model · context bar · compaction due · session · time
parts=()
if [[ -n "$model_name" ]]; then
  model_part="${DIM}${model_name}"
  [[ -n "$effort_level" ]] && model_part="${model_part} ${effort_level}"
  parts+=("${model_part}${RESET}")
fi
parts+=("$ctx_info")
[[ "$compaction_due" = true ]] && parts+=("${YELLOW}compaction due${RESET}")
[[ -n "$session_id" ]] && parts+=("${DIM}${session_id}${RESET}")
parts+=("$(format_time_color "$current_time")")
status_line=$(join_parts "${parts[@]}")
echo -e "$status_line"

# Line 2: 5h · 7d · Fable
# claude-usage --async matches gh-pr-lookup: print cache, detach refresh.
rate_parts=()
_usage_cmd=$(command -v claude-usage 2>/dev/null || command -v claude-usage.sh 2>/dev/null || true)
_usage=""
fable_part=""
if [[ -n "$_usage_cmd" ]]; then
  # Pass stdin's 5h/7d percentages so claude-usage can skip refresh when
  # nothing has burned on this account since the last successful fetch.
  _usage=$(STATUSLINE_5H="$rate_5h" STATUSLINE_7D="${rate_7d:-0}" \
    "$_usage_cmd" --async 2>/dev/null) || true
fi
if [[ -n "$_usage" ]]; then
  usage_fields=$(
    jq -r '[
      .ok != false,
      .fable // "",
      .resets_fable // "",
      .resets_7d // "",
      .error // "fetch failed",
      .five_hour // "",
      .resets_5h // "",
      .seven_day // ""
    ] | join("\u001f")' <<< "$_usage"
  )
  IFS=$'\037' read -r _usage_ok rate_fable resets_fable usage_resets_7d _usage_error usage_5h usage_resets_5h usage_7d <<< "$usage_fields"
  if [[ "$_usage_ok" != true ]]; then
    if [[ "$_usage_error" = "keychain unavailable" ]]; then
      if [[ -n "$rate_fable" ]]; then
        fable_part="${DIM}Fable ${rate_fable}% · stale${RESET}"
      else
        fable_part="${DIM}Fable unavailable${RESET}"
      fi
    elif [[ "$_usage_error" = "no python" ]]; then
      fable_part="${YELLOW}Fable: brew install python${RESET}"
    elif [[ "$_usage_error" = "token expired" ]] || [[ "$_usage_error" = "no login" ]] || [[ "$_usage_error" = "no token" ]] || [[ "$_usage_error" = "HTTP 401" ]]; then
      fable_part="${YELLOW}Fable: login required${RESET}"
    elif [[ "$_usage_error" = "HTTP 429" ]]; then
      if [[ -n "$rate_fable" ]]; then
        fable_part="${DIM}Fable ${rate_fable}% · rate limited${RESET}"
      else
        fable_part="${DIM}Fable: rate limited${RESET}"
      fi
    elif [[ -n "$rate_fable" ]]; then
      fable_part="${DIM}Fable ${rate_fable}% · fetch failed${RESET}"
    else
      fable_part="${DIM}Fable unavailable${RESET}"
    fi
  elif [[ -n "$rate_fable" ]]; then
    # A Fable reset that matches the 7d one shares its countdown. Compare within the usage
    # payload, and allow a minute of slack: the API rounds the two windows
    # independently, so the same reset arrives as 14:59:59 and 15:00:00. A real
    # divergence would be hours, never seconds.
    fable_shares_reset=false
    [[ -n "$resets_fable" ]] && [[ -n "$usage_resets_7d" ]] \
      && [[ "$(( resets_fable > usage_resets_7d ? resets_fable - usage_resets_7d : usage_resets_7d - resets_fable ))" -le 60 ]] \
      && fable_shares_reset=true
  fi
elif [[ -n "$_usage_cmd" ]]; then
  fable_part="${DIM}Fable unavailable${RESET}"
fi
# Stdin rate_limits is empty until the first API response. The usage
# fetch already ran for Fable and includes 5h/7d; use those only while
# stdin has nothing.
if [[ "${_usage_ok:-}" = true ]]; then
  if [[ -z "$rate_5h" ]]; then
    rate_5h=$usage_5h
    resets_5h=$usage_resets_5h
  fi
  if [[ -z "$rate_7d" ]]; then
    rate_7d=$usage_7d
    resets_7d=$usage_resets_7d
  fi
fi
cost_display="" cost_color=""
if [[ "${rate_5h:-0}" -ge 100 ]]; then
  if [[ -n "$raw_cost" ]]; then
    session_cost=$(printf "%.2f" "$raw_cost")
    blend "${red_rgb[@]}" "${alarm_rgb[@]}" $(( cost_cents * 100 / (cost_cents + 2000) ))
    printf -v cost_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
    cost_display="\$${session_cost}"
  fi
fi
rate=$(format_rate "$rate_5h" "$resets_5h" 18000 "$cost_display" "$cost_color" plus)
[[ -n "$rate" ]] && rate_parts+=("5h $rate")
usage_label="${DIM}Usage${RESET}"
# Without a five-hour entry the row has room for weekly batteries, and a countdown both
# weekly limits share moves next to the label.
if [[ -n "$rate_7d" && -z "$rate_5h" ]]; then
  weekly_battery 7d "$rate_7d" "$resets_7d" plus
  seven_day_part=$weekly_part
  [[ -n "${fable_shares_reset:-}" ]] && weekly_battery Fable "$rate_fable" "$resets_fable" && fable_part=$weekly_part
  format_countdown "$resets_7d" 604800
  if [[ "${fable_shares_reset:-}" = true ]]; then
    [[ -n "$countdown" ]] && usage_label+=" $countdown"
  else
    [[ -n "$countdown" ]] && seven_day_part+=" $countdown"
    if [[ "${fable_shares_reset:-}" = false ]]; then
      format_countdown "$resets_fable" 604800
      [[ -n "$countdown" ]] && fable_part+=" $countdown"
    fi
  fi
  rate_parts+=("$seven_day_part")
else
  rate=$(format_rate "$rate_7d" "$resets_7d" 604800 "" "" plus)
  [[ -n "$rate" ]] && rate_parts+=("7d $rate")
  if [[ "${fable_shares_reset:-}" = true ]]; then
    pace_gradient "$rate_fable" "$resets_fable" 604800
    printf -v fable_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
    fable_part="Fable ${fable_color}${rate_fable}%${RESET}"
  elif [[ "${fable_shares_reset:-}" = false ]]; then
    rate=$(format_rate "$rate_fable" "$resets_fable" 604800)
    [[ -n "$rate" ]] && fable_part="Fable $rate"
  fi
fi
[[ -n "$fable_part" ]] && rate_parts+=("$fable_part")
if (( ${#rate_parts[@]} )); then
  usage_line=$(join_parts "${rate_parts[@]}")
  echo -e "${usage_label} · $usage_line"
fi

# Location: the git line inside a repo, the ~ path outside one
if [[ -n "$git_status" ]]; then
  echo -e "$git_status"
else
  folder=$current_dir
  [[ "$folder" == "$HOME" || "$folder" == "$HOME"/* ]] && folder="~${folder#"$HOME"}"
  printf '\033[37m%s\033[0m\n' "$folder"
fi
