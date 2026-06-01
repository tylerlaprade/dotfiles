# resume — delay-launch a claude or codex session, keeping the machine awake
# Source from .zshrc / .bashrc:  source ~/Code/dotfiles/scripts/bin/resume.sh
# macOS-only: uses BSD `date -j -f` and `caffeinate`.
#
# No prompt arg → resumes the selected/latest session with prompt "continue".
# Prompt arg   → resumes the selected/latest session with that prompt.
# -n/--new     → starts a fresh session instead of resuming.
#
# Tool and time/duration may be passed in either order.
# Bare numbers are rejected as ambiguous — durations need a unit suffix.
#
# No time/duration → defaults to next rate-limit reset.
#   claude: reads /tmp/claude-rate-limits.json (written by statusline.sh).
#   codex:  reads the latest token_count event from the most recent
#           ~/.codex/sessions/*/*/*/rollout-*.jsonl.
# If 7d limit is exceeded, waits for 7d reset. Otherwise waits for next 5h
# reset. Errors if no snapshot exists, or if 7d is not exceeded and there
# is no active 5h window.
#
# Time/duration formats:
#   7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
#   3000s, 45m, 2h                duration in seconds/minutes/hours
#
# Options:
#   -s, --session ID_OR_NAME            resume a specific claude/codex session
#   -n, --new                           start a new session
#   -h, --help                          show help
#
# Usage:
#   resume claude                # resume last claude session at next 5h reset
#   resume codex 7p              # resume last codex session at 7:00 PM
#   resume 1220a claude          # resume last claude session at 12:20 AM
#   resume codex 3000s           # resume last codex session in 3000 seconds
#   resume codex -s 019... 7p    # resume a specific codex session at 7:00 PM
#   resume 730p claude "do X"    # resume last claude session at 7:30 PM with prompt "do X"
#   resume -n 730p claude "do X" # start new claude session at 7:30 PM with prompt "do X"

resume() {
  local session=""
  local new_session=0
  local -a args=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -s|--session)
        shift
        [ -n "$1" ] || { echo "resume: --session requires a session id or name" >&2; return 1; }
        session="$1" ;;
      --session=*)
        session="${1#--session=}"
        [ -n "$session" ] || { echo "resume: --session requires a session id or name" >&2; return 1; } ;;
      -n|--new)
        new_session=1 ;;
      -h|--help)
        _resume_help
        return 0 ;;
      --)
        shift
        args+=("$@")
        break ;;
      -*)
        echo "resume: unknown option '$1'" >&2
        return 1 ;;
      *)
        args+=("$1") ;;
    esac
    shift
  done
  set -- "${args[@]}"
  if [ -n "$session" ] && (( new_session )); then
    echo "resume: --session and --new cannot be used together" >&2
    return 1
  fi

  local a1="$1" a2="$2"

  local tool time_str
  if [[ $a1 == codex || $a1 == claude ]]; then
    tool="$a1"
    if [[ $a2 == codex || $a2 == claude ]]; then
      echo "resume: got two tool names; expected <codex|claude> [time|duration] [--session ID] [--new] [prompt]" >&2
      return 1
    fi
    time_str="$a2"
    if [ -n "$time_str" ]; then shift 2; else shift 1; fi
  elif [[ $a2 == codex || $a2 == claude ]]; then
    tool="$a2"; time_str="$a1"
    shift 2
  else
    _resume_help >&2
    return 1
  fi

  local delay
  if [ -z "$time_str" ]; then
    local seven_day resets_5h resets_7d now
    case "$tool" in
      claude)
        local rl_file="/tmp/claude-rate-limits.json"
        [ -f "$rl_file" ] || { echo "resume: no rate-limit snapshot at $rl_file — statusline must run at least once first" >&2; return 1; }
        IFS=$'\t' read -r seven_day resets_5h resets_7d < <(jq -r '[.seven_day // 0, .resets_5h // 0, .resets_7d // 0] | @tsv' "$rl_file") ;;
      codex)
        local latest
        latest=$(command ls ~/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null | sort -r | head -1)
        [ -n "$latest" ] || { echo "resume: no codex session rollouts in ~/.codex/sessions — run codex at least once first" >&2; return 1; }
        IFS=$'\t' read -r seven_day resets_5h resets_7d <<<"$(jq -rc 'select(.payload.rate_limits != null) | .payload.rate_limits | [(.secondary.used_percent // 0 | floor), (.primary.resets_at // 0), (.secondary.resets_at // 0)] | @tsv' "$latest" 2>/dev/null | tail -1)"
        [ -n "$resets_5h" ] || { echo "resume: no rate_limits data in latest codex rollout — session too short" >&2; return 1; } ;;
    esac
    now=$(date +%s)
    if [ "$seven_day" -ge 100 ]; then
      [ "$resets_7d" -le "$now" ] && { echo "resume: over 7d limit (${seven_day}%) but resets_7d=$resets_7d is not in the future — snapshot stale" >&2; return 1; }
      local target=$resets_7d
      [ "$resets_5h" -gt "$target" ] && target=$resets_5h
      delay=$(( target - now ))
    elif [ "$resets_5h" -le "$now" ]; then
      echo "resume: no active 5h window (resets_5h=$resets_5h, now=$now)" >&2
      return 1
    else
      delay=$(( resets_5h - now ))
    fi
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

  local action new=0
  local -a prompt_args=()
  if [ -n "$1" ]; then
    prompt_args=("$1")
  elif (( ! new_session )); then
    prompt_args=("continue")
  fi
  if (( new_session )); then
    action="Starting new"; new=1
  else
    action="Resuming"
  fi

  local -a cmd
  case "$tool" in
    codex)
      if (( new )); then
        cmd=(codex --dangerously-bypass-approvals-and-sandbox)
      else
        local codex_session="$session"
        if [ -z "$codex_session" ]; then
          # `codex resume` takes [SESSION_ID] [PROMPT] positionals. With --last and a
          # prompt, the prompt would land in the SESSION_ID slot. Extract the latest
          # rollout's UUID and pass it explicitly so the prompt lands correctly.
          local latest_rollout
          latest_rollout=$(command ls ~/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null | sort -r | head -1)
          if [ -z "$latest_rollout" ]; then
            echo "resume: no codex session rollouts in ~/.codex/sessions — cannot resume" >&2
            return 1
          fi
          codex_session=$(basename "$latest_rollout" | sed -E 's/^rollout-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}-(.+)\.jsonl$/\1/')
        fi
        cmd=(codex resume --dangerously-bypass-approvals-and-sandbox "$codex_session")
      fi ;;
    claude)
      cmd=(claude --dangerously-skip-permissions)
      if [ -n "$session" ]; then
        cmd+=(--resume "$session")
      else
        (( new )) || cmd+=(-c)
      fi ;;
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
        sleep 0.1
        i=$(( i + 1 ))
      done
    ) &
    spin_pid=$!
    trap "kill $spin_pid 2>/dev/null; printf \"\n\"; exit 130" INT TERM
    trap "kill $spin_pid 2>/dev/null" EXIT
    wait "$spin_pid" 2>/dev/null
    printf "\r\033[K\033]2;\007"
    exec "$@"
  ' _ "$label" "$target_clock" "$delay" "${cmd[@]}" "${prompt_args[@]}"
}

_resume_help() {
  cat <<'EOF'
Usage: resume <codex|claude> [time|duration] [options] [prompt]

Delay-launch a claude or codex session, keeping the machine awake.
Tool, time/duration, and options may be passed in any order.

No prompt arg resumes the selected/latest session with prompt "continue".
Prompt arg resumes the selected/latest session with that prompt.
Use -n/--new to start a fresh session instead of resuming.

Time/duration:
  7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
  3000s, 45m, 2h                duration in seconds/minutes/hours
  omitted                       next rate-limit reset

Options:
  -s, --session ID_OR_NAME       resume a specific claude/codex session
  -n, --new                      start a new session
  -h, --help                     show this help

Examples:
  resume claude
  resume codex 7p
  resume 1220a claude
  resume codex 3000s
  resume codex -s 019... 7p
  resume 730p claude "do X"
  resume -n 730p claude "do X"
EOF
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
