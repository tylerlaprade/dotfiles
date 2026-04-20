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
# No time/duration → defaults to next 5h rate-limit reset.
#   claude: reads /tmp/claude-rate-limits.json (written by statusline.sh).
#   codex:  reads the latest token_count event from the most recent
#           ~/.codex/sessions/*/*/*/rollout-*.jsonl.
# Errors if no snapshot exists, the 7d limit is over, or there is no
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
    local seven_day resets_5h now
    case "$tool" in
      claude)
        local rl_file="/tmp/claude-rate-limits.json"
        [ -f "$rl_file" ] || { echo "resume: no rate-limit snapshot at $rl_file — statusline must run at least once first" >&2; return 1; }
        IFS=$'\t' read -r seven_day resets_5h < <(jq -r '[.seven_day // 0, .resets_5h // 0] | @tsv' "$rl_file") ;;
      codex)
        local latest
        latest=$(command ls ~/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null | sort -r | head -1)
        [ -n "$latest" ] || { echo "resume: no codex session rollouts in ~/.codex/sessions — run codex at least once first" >&2; return 1; }
        IFS=$'\t' read -r seven_day resets_5h <<<"$(jq -rc 'select(.payload.rate_limits != null) | .payload.rate_limits | [(.secondary.used_percent // 0 | floor), (.primary.resets_at // 0)] | @tsv' "$latest" 2>/dev/null | tail -1)"
        [ -n "$resets_5h" ] || { echo "resume: no rate_limits data in latest codex rollout — session too short" >&2; return 1; } ;;
    esac
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
        # `codex resume` takes [SESSION_ID] [PROMPT] positionals. With --last and a
        # prompt, the prompt would land in the SESSION_ID slot. Extract the latest
        # rollout's UUID and pass it explicitly so the prompt lands correctly.
        local latest_rollout latest_uuid
        latest_rollout=$(command ls ~/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null | sort -r | head -1)
        if [ -z "$latest_rollout" ]; then
          echo "resume: no codex session rollouts in ~/.codex/sessions — cannot resume" >&2
          return 1
        fi
        latest_uuid=$(basename "$latest_rollout" | sed -E 's/^rollout-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}-(.+)\.jsonl$/\1/')
        cmd=(codex resume --dangerously-bypass-approvals-and-sandbox "$latest_uuid")
      fi ;;
    claude)
      cmd=(claude --dangerously-skip-permissions)
      (( new )) || cmd+=(-c) ;;
  esac

  local target_clock
  target_clock=$(date -r $(($(date +%s) + delay)) '+%I:%M %p')

  local label="$action $tool"
  caffeinate -ims sh -c '
    label=$1; clock=$2; delay=$3; shift 3
    end=$(( $(date +%s) + delay ))
    (
      i=0
      while :; do
        now=$(date +%s)
        rem=$(( end - now ))
        [ "$rem" -le 0 ] && break
        h=$(( rem / 3600 )); m=$(( (rem % 3600) / 60 )); s=$(( rem % 60 ))
        if [ "$h" -gt 0 ]; then t=$(printf "%dh%02dm" "$h" "$m")
        elif [ "$m" -gt 0 ]; then t=$(printf "%dm%02ds" "$m" "$s")
        else t=$(printf "%ds" "$s"); fi
        case $(( i % 10 )) in
          0) f="⠋" ;; 1) f="⠙" ;; 2) f="⠹" ;; 3) f="⠸" ;; 4) f="⠼" ;;
          5) f="⠴" ;; 6) f="⠦" ;; 7) f="⠧" ;; 8) f="⠇" ;; 9) f="⠏" ;;
        esac
        printf "\033]2;%s %s in %s\007" "$f" "$label" "$t"
        printf "\r\033[K%s %s in %s (%s)" "$f" "$label" "$t" "$clock"
        sleep 1
        i=$(( i + 1 ))
      done
    ) &
    spin_pid=$!
    trap "kill $spin_pid 2>/dev/null; printf \"\n\"; exit 130" INT TERM
    trap "kill $spin_pid 2>/dev/null" EXIT
    sleep "$delay"
    kill "$spin_pid" 2>/dev/null
    wait "$spin_pid" 2>/dev/null
    printf "\r\033[K"
    exec "$@"
  ' _ "$label" "$target_clock" "$delay" "${cmd[@]}" "$prompt"
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
