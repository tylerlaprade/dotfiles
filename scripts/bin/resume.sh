# resume — delay-launch a claude or codex session, keeping the machine awake
# Source from .zshrc / .bashrc:  source ~/Code/dotfiles/scripts/bin/resume.sh
#
# No prompt arg → resumes the last session with prompt "continue".
# Prompt arg   → starts a fresh session with that prompt.
#
# Tool and time/duration may be passed in either order.
# Bare numbers are rejected as ambiguous — durations need a unit suffix.
#
# Time/duration formats:
#   7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
#   3000s, 45m, 2h                duration in seconds/minutes/hours
#
# Usage:
#   resume codex 7p              # resume last codex session at 7:00 PM
#   resume 1220a claude          # resume last claude session at 12:20 AM
#   resume codex 3000s           # resume last codex session in 3000 seconds
#   resume 730p claude "do X"    # start new claude session at 7:30 PM with prompt "do X"

resume() {
  local a1="$1" a2="$2"
  shift 2

  local tool time_str
  case "$a1" in
    codex|claude) tool="$a1"; time_str="$a2" ;;
    *)
      case "$a2" in
        codex|claude) tool="$a2"; time_str="$a1" ;;
        *)
          echo "Usage: resume <codex|claude> <time|duration> [prompt]" >&2
          echo "       (tool and time may be in either order)" >&2
          return 1 ;;
      esac ;;
  esac

  local delay
  local lc="${time_str:l}"

  if [[ $lc == *am || $lc == *pm ]]; then
    local ampm="${lc: -2:1}"
    local time_num="${lc%??}"
    _resume_parse_clock "$time_num" "$ampm" || return 1
  elif [[ $lc == *[ap] ]]; then
    local ampm="${lc: -1}"
    local time_num="${lc%?}"
    _resume_parse_clock "$time_num" "$ampm" || return 1
  elif [[ $lc == *s ]]; then
    delay="${lc%s}"
  elif [[ $lc == *m ]]; then
    delay=$(( ${lc%m} * 60 ))
  elif [[ $lc == *h ]]; then
    delay=$(( ${lc%h} * 3600 ))
  elif [[ $lc == <-> ]]; then
    echo "resume: bare number '$time_str' is ambiguous — use 3000s, 45m, 2h, or a clock time like 7p" >&2
    return 1
  else
    echo "resume: unrecognized time/duration '$time_str' — use 3000s, 45m, 2h, or a clock time like 7p" >&2
    return 1
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

_resume_parse_clock() {
  local time_num="$1" ampm="$2"
  local hour min
  if [[ $time_num != <-> ]]; then
    echo "resume: invalid clock time '$time_num$ampm'" >&2
    return 1
  fi
  if [ ${#time_num} -le 2 ]; then
    hour=$time_num min=0
  else
    min=${time_num: -2}
    hour=${time_num%??}
  fi
  case $ampm in
    p) [ "$hour" -ne 12 ] && hour=$((hour + 12)) ;;
    a) [ "$hour" -eq 12 ] && hour=0 ;;
  esac
  local target_ts now_ts
  target_ts=$(date -j -f "%H:%M:%S" "$(printf '%02d:%02d:00' "$hour" "$min")" +%s)
  now_ts=$(date +%s)
  delay=$((target_ts - now_ts))
  [ "$delay" -le 0 ] && delay=$((delay + 86400))
}
