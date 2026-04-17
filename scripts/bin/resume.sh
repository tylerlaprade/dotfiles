# resume — delay-launch a claude or codex session, keeping the machine awake
# Source from .zshrc / .bashrc:  source ~/Code/dotfiles/scripts/bin/resume.sh
# macOS-only: uses BSD `date -j -f` and `caffeinate`.
#
# No prompt arg → resumes the last session with prompt "continue".
# Prompt arg   → starts a fresh session with that prompt.
#
# Tool and time/duration may be passed in either order.
# Bare numbers are rejected as ambiguous — durations need a unit suffix.
#
# No time/duration → defaults to next 5h rate-limit reset, read from
# /tmp/claude-rate-limits.json (written by statusline.sh). Errors if the
# snapshot is missing, the 7d limit is already over, or there is no
# active 5h window.
#
# Time/duration formats:
#   7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
#   3000s, 45m, 2h                duration in seconds/minutes/hours
#
# Usage:
#   resume claude                # resume last claude session at next 5h reset
#   resume codex 7p              # resume last codex session at 7:00 PM
#   resume 1220a claude          # resume last claude session at 12:20 AM
#   resume codex 3000s           # resume last codex session in 3000 seconds
#   resume 730p claude "do X"    # start new claude session at 7:30 PM with prompt "do X"

resume() {
  local a1="$1" a2="$2"

  local tool time_str
  if [[ $a1 == codex || $a1 == claude ]]; then
    tool="$a1"
    if [[ $a2 == codex || $a2 == claude ]]; then
      echo "resume: got two tool names; expected <codex|claude> [time|duration] [prompt]" >&2
      return 1
    fi
    time_str="$a2"
    if [ -n "$time_str" ]; then shift 2; else shift 1; fi
  elif [[ $a2 == codex || $a2 == claude ]]; then
    tool="$a2"; time_str="$a1"
    shift 2
  else
    echo "Usage: resume <codex|claude> [time|duration] [prompt]" >&2
    echo "       (tool and time may be in either order; omit time to wait for next 5h reset)" >&2
    return 1
  fi

  local delay
  if [ -z "$time_str" ]; then
    [ "$tool" = "claude" ] || { echo "resume: no-time default only supported for claude (codex rate-limit snapshot not wired up yet)" >&2; return 1; }
    local rl_file="/tmp/claude-rate-limits.json"
    [ -f "$rl_file" ] || { echo "resume: no rate-limit snapshot at $rl_file — statusline must run at least once first" >&2; return 1; }
    local seven_day resets_5h now
    IFS=$'\t' read -r seven_day resets_5h < <(jq -r '[.seven_day // 0, .resets_5h // 0] | @tsv' "$rl_file")
    now=$(date +%s)
    [ "$seven_day" -ge 100 ] && { echo "resume: over 7d limit (${seven_day}%) — not resuming" >&2; return 1; }
    [ "$resets_5h" -le "$now" ] && { echo "resume: no active 5h window (resets_5h=$resets_5h, now=$now)" >&2; return 1; }
    delay=$(( resets_5h - now ))
  else
    local num rest
    num="${time_str%%[!0-9]*}"
    rest="${time_str#"$num"}"
    if [ -z "$num" ] || [ -z "$rest" ]; then
      if [[ $time_str =~ ^[0-9]+$ ]]; then
        echo "resume: bare number '$time_str' is ambiguous — use 3000s, 45m, 2h, or a clock time like 7p" >&2
      else
        echo "resume: unrecognized time/duration '$time_str' — use 3000s, 45m, 2h, or a clock time like 7p" >&2
      fi
      return 1
    fi
    case "$rest" in
      [sS])                 delay="$num" ;;
      [mM])                 delay=$(( num * 60 )) ;;
      [hH])                 delay=$(( num * 3600 )) ;;
      [aApP]|[aApP][mM])    delay=$(_resume_clock_delay "$num" "${rest:0:1}") || return 1 ;;
      *)
        echo "resume: unrecognized time/duration '$time_str' — use 3000s, 45m, 2h, or a clock time like 7p" >&2
        return 1 ;;
    esac
  fi

  local prompt action new=0
  if [ -n "$1" ]; then
    prompt="$1"; action="Starting new"; new=1
  else
    prompt="continue"; action="Resuming"
  fi

  local -a cmd
  case "$tool" in
    codex)
      if (( new )); then
        cmd=(codex --dangerously-bypass-approvals-and-sandbox)
      else
        cmd=(codex resume --last --dangerously-bypass-approvals-and-sandbox)
      fi ;;
    claude)
      cmd=(claude --dangerously-skip-permissions)
      (( new )) || cmd+=(-c) ;;
  esac

  echo "$action $tool in ${delay}s ($(date -r $(($(date +%s) + delay)) '+%I:%M %p'))"
  caffeinate -ims sh -c 'sleep "$1"; shift; exec "$@"' _ "$delay" "${cmd[@]}" "$prompt"
}

_resume_clock_delay() {
  local time_num="$1" ampm="$2" hour min
  if [ ${#time_num} -le 2 ]; then
    hour=$time_num min=0
  else
    min=${time_num: -2}
    hour=${time_num%??}
  fi
  case $ampm in
    p|P) [ "$hour" -ne 12 ] && hour=$((hour + 12)) ;;
    a|A) [ "$hour" -eq 12 ] && hour=0 ;;
  esac
  local target_ts now_ts delay
  target_ts=$(date -j -f "%H:%M:%S" "$(printf '%02d:%02d:00' "$hour" "$min")" +%s)
  now_ts=$(date +%s)
  delay=$((target_ts - now_ts))
  [ "$delay" -le 0 ] && delay=$((delay + 86400))
  printf '%s\n' "$delay"
}
