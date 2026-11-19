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

# Ghostty's default colors, which also stand in for any terminal whose theme is unreadable.
fallback_palette=(255 255 255 129 162 190 181 189 104 240 198 116 204 102 102 255 0 0)
ghostty_default_background=282c34

# Prints two lines, given the background, foreground, blue, green, yellow, and red as hex. The first is
# the foreground, blue, green, yellow, red, and alarm red as r g b each. The second is the steps per
# gradient, then each step's r g b along blue→green, green→yellow, yellow→red, red→alarm, and yellow→alarm.
# On a dark background a color under 4.5:1 moves toward white until it reaches 4.5:1, and each gradient
# is just its two ends. On a light background the colors, and each gradient at every fifth percent, are
# redrawn in their own hue at the lightest shade that reaches 4.5:1, as saturated as that shade allows.
readable_palette() {
  jq -rn '
    def channel: . / 255 | if . <= 0.04045 then . / 12.92 else pow((. + 0.055) / 1.055; 2.4) end;
    def luminance: 0.2126 * (.[0] | channel) + 0.7152 * (.[1] | channel) + 0.0722 * (.[2] | channel);
    def contrast($other): [luminance, ($other | luminance)] | (max + 0.05) / (min + 0.05);
    def rgb: ltrimstr("#") | ascii_downcase | explode | map(if . >= 97 then . - 87 else . - 48 end)
      | [.[0] * 16 + .[1], .[2] * 16 + .[3], .[4] * 16 + .[5]];
    def toward($target; $percent): [., $target] | transpose | map(.[0] + (.[1] - .[0]) * $percent / 100 | round);
    def blend($target; $percent): [., $target] | transpose | map(.[0] + ((.[1] - .[0]) * $percent / 100 | trunc));
    def readable($background; $light):
      (if $light then [0, 0, 0] else [255, 255, 255] end) as $extreme
      | . as $color
      | first(range(0; 101) as $percent | $color | toward($extreme; $percent) | select(contrast($background) >= 4.5))
        // $extreme;
    def hue:
      map(channel) as [$r, $g, $b]
      | [0.4122214708 * $r + 0.5363325363 * $g + 0.0514459929 * $b,
         0.2119034982 * $r + 0.6806995451 * $g + 0.1073969566 * $b,
         0.0883024619 * $r + 0.2817188376 * $g + 0.6299787005 * $b]
      | map(cbrt) as [$l, $m, $s]
      | atan2(0.0259040371 * $l + 0.7827717662 * $m - 0.8086757660 * $s; 1.9779984951 * $l - 2.4285922050 * $m + 0.4505937099 * $s);
    def linear_rgb($lightness; $chroma; $hue):
      ($chroma * ($hue | cos)) as $a | ($chroma * ($hue | sin)) as $b
      | [$lightness + 0.3963377774 * $a + 0.2158037573 * $b,
         $lightness - 0.1055613458 * $a - 0.0638541728 * $b,
         $lightness - 0.0894841775 * $a - 1.2914855480 * $b]
      | map(. * . * .) as [$l, $m, $s]
      | [4.0767416621 * $l - 3.3077115913 * $m + 0.2309699292 * $s,
         -1.2684380046 * $l + 2.6097574011 * $m - 0.3413193965 * $s,
         -0.0041960863 * $l - 0.7034186147 * $m + 1.7076147010 * $s];
    def in_gamut: all(.[]; . >= -0.0001 and . <= 1.0001);
    def encoded: map([[., 0] | max, 1] | min | if . <= 0.0031308 then 12.92 * . else 1.055 * pow(.; 1 / 2.4) - 0.055 end | . * 255 | round);
    def vivid($lightness; $hue):
      (reduce range(12) as $_ ([0, 0.4]; ((.[0] + .[1]) / 2) as $chroma
        | if linear_rgb($lightness; $chroma; $hue) | in_gamut then [$chroma, .[1]] else [.[0], $chroma] end)
        | .[0]) as $chroma
      | linear_rgb($lightness; $chroma; $hue) | encoded;
    def redrawn($background):
      hue as $hue
      | reduce range(14) as $_ ([0, 1]; ((.[0] + .[1]) / 2) as $lightness
          | if vivid($lightness; $hue) | contrast($background) >= 4.5 then [$lightness, .[1]] else [.[0], $lightness] end)
      | vivid(.[0]; $hue);
    $ARGS.positional | map(rgb) as [$background, $foreground, $blue, $green, $yellow, $red]
    # 0.179 is the background luminance where black and white text have equal contrast.
    | ($background | luminance > 0.179) as $light
    | [$blue, $green, $yellow, $red, [255, 0, 0]] | map(readable($background; $light)) as $colors
    | $colors as [$b, $g, $y, $r, $alarm]
    | [[$b, $g], [$g, $y], [$y, $r], [$r, $alarm], [$y, $alarm]] as $gradients
    | if $light then
        [$foreground, ($colors[] | redrawn($background))],
        [20, ($gradients[] as [$from, $to] | range(0; 101; 5) as $percent | $from | blend($to; $percent) | redrawn($background))]
      else
        [$foreground, $colors],
        [1, $gradients]
      end
    | flatten | join(" ")' --args "$@"
}

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

# Each resolver sets theme_colors to the background, foreground, blue, green, yellow, and red as hex.
resolve_rex_palette() {
  local appearance theme_id colors
  local themes=${REX_BIN_DIR:-}
  themes=${themes%%/Contents/Helpers/*}/Contents/Resources/SuperUI_SuperDesignTokens.bundle/Contents/Resources/themes.json
  palette_sources+=("$HOME/Library/Preferences/com.superlogical.rex.plist" "$themes")
  appearance=$(defaults read com.superlogical.rex Workspace.appearance 2>/dev/null)
  case "$appearance" in
    light|dark) ;;
    auto|'') system_appearance ;;
    *) return 1 ;;
  esac
  theme_id=$(defaults read com.superlogical.rex "Workspace.${appearance}ThemeID" 2>/dev/null) || return
  colors=$(jq -r --arg id "$theme_id" '
    first(.[] | select(.id == $id) | .source | [.background, .foreground, .ansi[4], .ansi[2], .ansi[3], .ansi[1]])
    | join(" ")' "$themes" 2>/dev/null)
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
  local -a colors=([256]=$ghostty_default_background) config_colors=() entries=() theme_files=()
  for index in 257 4 2 3 1; do
    printf -v hex '%02x%02x%02x' "${fallback_palette[@]:offset:3}"
    colors[index]=$hex
    (( offset += 3 ))
  done
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
  theme_colors=("${colors[256]}" "${colors[257]}" "${colors[4]}" "${colors[2]}" "${colors[3]}" "${colors[1]}")
}

# Sets segments to the five gradients with just their two ends, as readable_palette does on a dark background.
end_point_segments() {
  local blue=("${palette[@]:3:3}") green=("${palette[@]:6:3}") yellow=("${palette[@]:9:3}") red=("${palette[@]:12:3}") alarm=("${palette[@]:15:3}")
  segments=(1 "${blue[@]}" "${green[@]}" "${green[@]}" "${yellow[@]}" "${yellow[@]}" "${red[@]}" "${red[@]}" "${alarm[@]}" "${yellow[@]}" "${alarm[@]}")
}

load_palette() {
  local resolver=$1 source joined_sources readable='' palette_cache=${TMPDIR:-/tmp}
  palette_cache=${palette_cache%/}/statusline-palette-${TERM_PROGRAM:-}
  palette=()
  if [[ -f "$palette_cache" ]]; then
    { IFS=$'\037' read -r -a palette_sources; read -r -a palette; read -r -a segments; } < "$palette_cache"
    for source in "${palette_sources[@]}"; do
      [[ "$palette_cache" -nt "$source" ]] || { palette=(); break; }
    done
  fi
  (( ${#palette[@]} )) && return
  palette_sources=("${BASH_SOURCE[0]}") theme_colors=()
  "$resolver" && readable=$(readable_palette "${theme_colors[@]}" 2>/dev/null)
  if [[ -n "$readable" ]]; then
    { read -r -a palette; read -r -a segments; } <<< "$readable"
  else
    palette=("${fallback_palette[@]}")
    end_point_segments
  fi
  printf -v joined_sources '%s\037' "${palette_sources[@]}"
  printf '%s\n%s\n%s\n' "${joined_sources%$'\037'}" "${palette[*]}" "${segments[*]}" > "$palette_cache.$$" \
    && mv -f "$palette_cache.$$" "$palette_cache"
}

case "${TERM_PROGRAM:-}" in
  rex) load_palette resolve_rex_palette ;;
  ghostty) load_palette resolve_ghostty_palette ;;
  *)
    palette=("${fallback_palette[@]}")
    end_point_segments
    ;;
esac
foreground_rgb=("${palette[@]:0:3}") blue_rgb=("${palette[@]:3:3}") green_rgb=("${palette[@]:6:3}")
yellow_rgb=("${palette[@]:9:3}") alarm_rgb=("${palette[@]:15:3}")
printf -v FOREGROUND '\033[38;2;%d;%d;%dm' "${foreground_rgb[@]}"
printf -v YELLOW '\033[38;2;%d;%d;%dm' "${yellow_rgb[@]}"
printf -v pace_mark '\033[1;38;2;%d;%d;%dm│\033[0m' "${foreground_rgb[@]}"

set_color() {
  r=$1 g=$2 b=$3
}

# Sets r, g, b to the color a percent of the way from the first r g b to the second.
blend() {
  r=$(( $1 + ($4 - $1) * $7 / 100 ))
  g=$(( $2 + ($5 - $2) * $7 / 100 ))
  b=$(( $3 + ($6 - $3) * $7 / 100 ))
}

blue_to_green=0 green_to_yellow=1 yellow_to_red=2 red_to_alarm=3 yellow_to_alarm=4

# Sets r, g, b to the color a percent along one of the gradients saved with the palette, blending
# between its two nearest steps.
blend_gradient() {
  local steps=${segments[0]} position=$(( $2 * segments[0] ))
  local index=$(( 1 + ($1 * (steps + 1) + position / 100) * 3 ))
  if (( position % 100 == 0 )); then
    set_color "${segments[@]:index:3}"
  else
    blend "${segments[@]:index:6}" $(( position % 100 ))
  fi
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
      blend_gradient "$blue_to_green" $(( (val - blue_floor) * 100 / (green_end - blue_floor) ))
    fi
  elif [[ "$val" -le "$green_end" ]]; then
    set_color "${green_rgb[@]}"
  elif [[ "$val" -le "$yellow_pt" ]]; then
    blend_gradient "$green_to_yellow" $(( (val - green_end) * 100 / (yellow_pt - green_end) ))
  elif [[ "$val" -le "$red_pt" ]]; then
    blend_gradient "$yellow_to_red" $(( (val - yellow_pt) * 100 / (red_pt - yellow_pt) ))
  else
    blend_gradient "$red_to_alarm" $(( (val - red_pt) * 100 / (val - red_pt + k) ))
  fi
}

rate_usage_gradient() {
  local val=$1
  if [[ "$val" -le 55 ]]; then
    set_color "${green_rgb[@]}"
  elif [[ "$val" -le 75 ]]; then
    blend_gradient "$green_to_yellow" $(( (val - 55) * 100 / 20 ))
  elif [[ "$val" -le 95 ]]; then
    blend_gradient "$yellow_to_alarm" $(( (val - 75) * 100 / 20 ))
  else
    set_color "${alarm_rgb[@]}"
  fi
}

# Sets battery to a 10-cell bar in the color the caller left in r, g, b, with a
# smooth transition cell, and battery_color to that color. An optional cell index becomes a
# pace mark in the theme's foreground. Sets variables instead of printing, so a render starts no subshell.
build_battery() {
  local pct=$1 mark_cell=${2:--1} filled=$(( $1 * 10 / 100 )) transition=░ cell
  [[ "$filled" -gt 10 ]] && filled=10
  [[ $(( pct * 10 % 100 )) -ge 50 ]] && transition=▒
  printf -v battery_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
  battery=$battery_color
  for (( cell = 0; cell < 10; cell++ )); do
    if [[ "$cell" -eq "$mark_cell" ]]; then
      battery+=$pace_mark$battery_color
    elif [[ "$cell" -lt "$filled" ]]; then
      battery+=▓
    elif [[ "$cell" -eq "$filled" ]]; then
      battery+=$transition
    else
      battery+=░
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
  local night=0
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
  [[ "$night" -eq 1 ]] && P0=$P_blue
  if [[ "$secs" -lt "$P1" ]]; then           # start_color -> green
    phase_start=$P0
    t=$(( (secs - phase_start) * 100 / (P1 - P0) ))
    if [[ "$night" -eq 1 ]]; then
      blend_gradient "$blue_to_green" "$t"
    else
      blend "${foreground_rgb[@]}" "${green_rgb[@]}" "$t"
    fi
  elif [[ "$secs" -lt "$P2" ]]; then         # green -> yellow
    phase_start=$P1
    t=$(( (secs - phase_start) * 100 / (P2 - P1) ))
    blend_gradient "$green_to_yellow" "$t"
  elif [[ "$secs" -lt "$P3" ]]; then         # yellow -> alarm red + BOLD
    phase_start=$P2
    t=$(( (secs - phase_start) * 100 / (P3 - P2) ))
    blend_gradient "$yellow_to_alarm" "$t"
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

  local time_remaining=$(( resets - now ))
  [[ "$time_remaining" -lt 0 ]] && time_remaining=0
  local time_elapsed=$(( window_secs - time_remaining ))
  [[ "$time_elapsed" -lt 60 ]] && time_elapsed=60

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

  if [[ "$time_remaining" -gt 0 ]]; then
    local days=$(( time_remaining / 86400 ))
    local hrs=$(( (time_remaining % 86400) / 3600 ))
    local mins=$(( (time_remaining % 3600) / 60 ))
    local remaining=""
    if [[ "$days" -gt 0 ]]; then
      remaining="${days}d ${hrs}h"
    elif [[ "$hrs" -gt 0 ]]; then
      remaining="${hrs}h ${mins}m"
    else
      remaining="${mins}m"
    fi
    # Countdown color by how soon the reset is: blue just after one, then green,
    # yellow, and red as the next nears.
    severity_gradient $(( time_elapsed * 100 / window_secs )) 55 80 95 80 20
    local countdown_color
    printf -v countdown_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
    info="${info} (resets in ${countdown_color}${remaining}${RESET})"
  fi

  echo "$info"
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
    # Same weekly reset as 7d: omit the duplicate countdown. Compare within the usage
    # payload, and allow a minute of slack: the API rounds the two windows
    # independently, so the same reset arrives as 14:59:59 and 15:00:00. A real
    # divergence would be hours, never seconds.
    if [[ -n "$resets_fable" ]] && [[ -n "$usage_resets_7d" ]] \
       && [[ "$(( resets_fable > usage_resets_7d ? resets_fable - usage_resets_7d : usage_resets_7d - resets_fable ))" -le 60 ]]; then
      pace_gradient "$rate_fable" "$resets_fable" 604800
      fable_color=$(printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b")
      fable_part="Fable ${fable_color}${rate_fable}%${RESET}"
    else
      rate=$(format_rate "$rate_fable" "$resets_fable" 604800)
      [[ -n "$rate" ]] && fable_part="Fable $rate"
    fi
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
    blend_gradient "$red_to_alarm" $(( cost_cents * 100 / (cost_cents + 2000) ))
    printf -v cost_color '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
    cost_display="\$${session_cost}"
  fi
fi
rate=$(format_rate "$rate_5h" "$resets_5h" 18000 "$cost_display" "$cost_color" plus)
[[ -n "$rate" ]] && rate_parts+=("5h $rate")
# Without a five-hour entry the row has room for a weekly battery in the pace
# color, with a mark where an even burn would put the week by now.
if [[ -n "$rate_7d" && -z "$rate_5h" ]]; then
  week_left=$(( ${resets_7d:-0} - now ))
  pace_cell=-1
  if [[ "$week_left" -gt 0 ]]; then
    [[ "$week_left" -gt 604800 ]] && week_left=604800
    pace_cell=$(( (604800 - week_left) * 10 / 604800 ))
  fi
  pace_gradient "$rate_7d" "$resets_7d" 604800
  build_battery "$rate_7d" "$pace_cell"
  week_percent="${rate_7d}%"
  [[ "$rate_7d" -ge 100 ]] && week_percent+="+"
  rate=$(format_rate "$rate_7d" "$resets_7d" 604800 "$week_percent" "$battery_color")
  rate_parts+=("7d ${battery}${RESET} $rate")
else
  rate=$(format_rate "$rate_7d" "$resets_7d" 604800 "" "" plus)
  [[ -n "$rate" ]] && rate_parts+=("7d $rate")
fi
[[ -n "$fable_part" ]] && rate_parts+=("$fable_part")
if (( ${#rate_parts[@]} )); then
  usage_line=$(join_parts "${rate_parts[@]}")
  echo -e "${DIM}Usage${RESET} · $usage_line"
fi

# Location: the git line inside a repo, the ~ path outside one
if [[ -n "$git_status" ]]; then
  echo -e "$git_status"
else
  folder=$current_dir
  [[ "$folder" == "$HOME" || "$folder" == "$HOME"/* ]] && folder="~${folder#"$HOME"}"
  printf '\033[37m%s\033[0m\n' "$folder"
fi
