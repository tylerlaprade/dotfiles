# resume — delay-launch a claude, codex, or grok session
# Source from .zshrc / .bashrc:  source ~/Code/dotfiles/scripts/bin/resume.sh
# macOS-only: uses BSD `date -j -f`. --wake uses `sudo pmset schedule wake`.
#
# No prompt arg → resumes this terminal tab's last session with prompt "continue".
# Prompt arg   → resumes this terminal tab's last session with that prompt.
# -s/--session → overrides tab-local selection with an explicit session.
# -n/--new     → starts a fresh session instead of resuming.
# -w/--wake    → schedule a one-shot power wake at the target time (needs admin).
#
# Tool and time/duration may be passed in either order.
# Bare numbers are rejected as ambiguous — durations need a unit suffix.
#
# No time/duration → defaults to next rate-limit reset.
#   claude: runs claude-usage.sh (claude.ai subscription login, not an API key).
#           Waits on the Fable weekly cap if that is at 100%, else the all-models
#           7d window if that is at 100%, else the next 5h reset.
#   codex:  reads the latest token_count event from the most recent
#           ~/.codex/sessions/*/*/*/rollout-*.jsonl.
#   grok:   reads the latest "billing: fetched credits config" event from
#           ~/.grok/logs/unified.jsonl (creditUsagePercent + billingPeriodEnd).
# Codex: if 7d limit is exceeded, waits for 7d reset; otherwise waits for next
# 5h reset. Errors if no snapshot exists, or if 7d is not exceeded and there
# is no active 5h window.
# Grok: weekly/monthly credit allotment (not a rolling 5h window). If usage
# is at 100%, waits for period end; if under, starts immediately (delay 0).
#
# Time/duration formats:
#   7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
#   3000s, 45m, 2h, 3d            duration in seconds/minutes/hours/days
#
# Options:
#   -s, --session ID_OR_NAME            resume a specific claude/codex/grok session
#   -n, --new                           start a new session
#   -w, --wake                          schedule a Mac wake at the target time
#   -h, --help                          show help
#
# Usage:
#   resume claude                # resume this tab's last claude session at next reset
#   resume claude 3d             # wait three days
#   resume --wake claude 3d      # same, and schedule a Mac wake (needs admin)
#   resume grok                  # if over credits, wait for period end; else now
#   resume codex 7p              # resume this tab's last codex session at 7:00 PM
#   resume grok 7p               # resume this tab's last grok session at 7:00 PM
#   resume 1220a claude          # resume this tab's last claude session at 12:20 AM
#   resume codex 3000s           # resume this tab's last codex session in 3000 seconds
#   resume codex -s 019... 7p    # resume a specific codex session at 7:00 PM
#   resume 730p claude "do X"    # resume this tab's claude session with prompt "do X"
#   resume -n 730p claude "do X" # start new claude session at 7:30 PM with prompt "do X"

resume() {
  local session=""
  local new_session=0
  local wake=0
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
      -w|--wake)
        wake=1 ;;
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
  if [[ $a1 == codex || $a1 == claude || $a1 == grok ]]; then
    tool="$a1"
    if [[ $a2 == codex || $a2 == claude || $a2 == grok ]]; then
      echo "resume: got two tool names; expected <codex|claude|grok> [time|duration] [--session ID] [--new] [prompt]" >&2
      return 1
    fi
    time_str="$a2"
    if [ -n "$time_str" ]; then shift 2; else shift 1; fi
  elif [[ $a2 == codex || $a2 == claude || $a2 == grok ]]; then
    tool="$a2"; time_str="$a1"
    shift 2
  else
    _resume_help >&2
    return 1
  fi

  local selected_session="$session"
  if (( ! new_session )) && [ -z "$selected_session" ]; then
    selected_session=$(_resume_last_session "$tool" "$$") || return 1
  fi

  local delay
  if [ -z "$time_str" ]; then
    local now
    now=$(date +%s)
    if [[ $tool == grok ]]; then
      # Grok logs billing snapshots (creditUsagePercent + period end) into its
      # unified log whenever a session fetches credits. No separate 5h window.
      local log_file="${HOME}/.grok/logs/unified.jsonl"
      [ -f "$log_file" ] || { echo "resume: no grok log at $log_file — run grok at least once first" >&2; return 1; }
      local used_pct period_end
      IFS=$'\t' read -r used_pct period_end < <(jq -rc '
        select(.msg == "billing: fetched credits config")
        | .ctx.config as $c
        | [
            ($c.creditUsagePercent // 0 | floor),
            (
              ($c.billingPeriodEnd // $c.currentPeriod.end // empty)
              | sub("\\.[0-9]+"; "")
              | sub("\\+00:00$"; "Z")
              | fromdateiso8601
            )
          ]
        | @tsv
      ' "$log_file" 2>/dev/null | tail -1)
      [ -n "$period_end" ] || { echo "resume: no billing credits data in $log_file — run grok at least once first" >&2; return 1; }
      if [ "$used_pct" -ge 100 ]; then
        [ "$period_end" -le "$now" ] && { echo "resume: over credit limit (${used_pct}%) but period_end=$period_end is not in the future — snapshot stale" >&2; return 1; }
        delay=$(( period_end - now ))
      else
        delay=0
      fi
    else
      local seven_day resets_5h resets_7d
      case "$tool" in
        claude)
          local fable=0 resets_fable=0
          IFS=$'\t' read -r seven_day resets_5h resets_7d fable resets_fable < <(_resume_claude_usage) || return 1 ;;
        codex)
          local latest
          latest=$(command ls ~/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null | sort -r | head -1)
          [ -n "$latest" ] || { echo "resume: no codex session rollouts in ~/.codex/sessions — run codex at least once first" >&2; return 1; }
          IFS=$'\t' read -r seven_day resets_5h resets_7d <<<"$(jq -rc 'select(.payload.rate_limits != null) | .payload.rate_limits | [(.secondary.used_percent // 0 | floor), (.primary.resets_at // 0), (.secondary.resets_at // 0)] | @tsv' "$latest" 2>/dev/null | tail -1)"
          [ -n "$resets_5h" ] || { echo "resume: no rate_limits data in latest codex rollout — session too short" >&2; return 1; } ;;
      esac
      local weekly_hit=0 target=0
      if [ "$tool" = claude ] && [ "${fable:-0}" -ge 100 ]; then
        [ "$resets_fable" -le "$now" ] && { echo "resume: over Fable limit (${fable}%) but resets_fable=$resets_fable is not in the future — snapshot stale" >&2; return 1; }
        weekly_hit=1
        target=$resets_fable
      fi
      if [ "$seven_day" -ge 100 ]; then
        [ "$resets_7d" -le "$now" ] && { echo "resume: over 7d limit (${seven_day}%) but resets_7d=$resets_7d is not in the future — snapshot stale" >&2; return 1; }
        weekly_hit=1
        [ "$resets_7d" -gt "$target" ] && target=$resets_7d
      fi
      if (( weekly_hit )); then
        [ "$resets_5h" -gt "$target" ] && target=$resets_5h
        delay=$(( target - now ))
      elif [ "$resets_5h" -le "$now" ]; then
        echo "resume: no active 5h window (resets_5h=$resets_5h, now=$now)" >&2
        return 1
      else
        delay=$(( resets_5h - now ))
      fi
    fi
  else
    local num rest
    num="${time_str%%[!0-9]*}"
    rest="${time_str#"$num"}"
    if [ -z "$num" ] || [ -z "$rest" ]; then
      if [[ $time_str =~ ^[0-9]+$ ]]; then
        echo "resume: bare number '$time_str' is ambiguous — use 3000s, 45m, 2h, 3d, or a clock time like 7p" >&2
      else
        echo "resume: unrecognized time/duration '$time_str' — use 3000s, 45m, 2h, 3d, or a clock time like 7p" >&2
      fi
      return 1
    fi
    case "$rest" in
      [sS])                 delay="$num" ;;
      [mM])                 delay=$(( num * 60 )) ;;
      [hH])                 delay=$(( num * 3600 )) ;;
      [dD])                 delay=$(( num * 86400 )) ;;
      [aApP]|[aApP][mM])    delay=$(_resume_clock_delay "$num" "${rest:0:1}") || return 1 ;;
      *)
        echo "resume: unrecognized time/duration '$time_str' — use 3000s, 45m, 2h, 3d, or a clock time like 7p" >&2
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
        cmd=(codex resume --dangerously-bypass-approvals-and-sandbox "$selected_session")
      fi ;;
    claude)
      cmd=(claude --dangerously-skip-permissions)
      (( new )) || cmd+=(--resume "$selected_session") ;;
    grok)
      # Config may already set permission_mode=always-approve; pass it explicitly
      # so delayed launches stay yolo even if config differs on another machine.
      cmd=(grok --always-approve)
      (( new )) || cmd+=(--resume "$selected_session") ;;
  esac

  local target_clock
  target_clock=$(date -r $(($(date +%s) + delay)) '+%I:%M %p')

  local label="$action $tool"
  local wake_when=""
  _resume_wake_stamp=""
  if (( wake )) && [ "$delay" -gt 0 ]; then
    _resume_schedule_wake $(( $(date +%s) + delay )) || true
    wake_when=$_resume_wake_stamp
  fi
  _resume_sleep_until "$label" "$target_clock" "$delay" "$$" "$wake_when" "${cmd[@]}" "${prompt_args[@]}"
}

_resume_help() {
  cat <<'EOF'
Usage: resume <codex|claude|grok> [time|duration] [options] [prompt]

Delay-launch a claude, codex, or grok session.
Tool, time/duration, and options may be passed in any order.

No prompt arg resumes this terminal tab's last session with prompt "continue".
Prompt arg resumes this terminal tab's last session with that prompt.
Use -n/--new to start a fresh session instead of resuming.
Use -s/--session to override tab-local selection.
Use -w/--wake to schedule a Mac wake at the target time (needs admin).

Time/duration:
  7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
  3000s, 45m, 2h, 3d            duration in seconds/minutes/hours/days
  omitted                       next rate-limit reset

Options:
  -s, --session ID_OR_NAME       resume a specific claude/codex/grok session
  -n, --new                      start a new session
  -w, --wake                     schedule a Mac wake at the target time
  -h, --help                     show this help

Examples:
  resume claude
  resume grok
  resume claude 3d
  resume --wake claude 3d
  resume codex 7p
  resume grok 7p
  resume 1220a claude
  resume codex 3000s
  resume codex -s 019... 7p
  resume 730p claude "do X"
  resume -n 730p claude "do X"
EOF
}

_resume_claude_usage() {
  local helper
  helper=$(command -v claude-usage.sh 2>/dev/null) || helper=$(command -v claude-usage 2>/dev/null) || true
  if [ -z "$helper" ]; then
    echo "resume: claude-usage.sh is not on PATH — install it or pass a time" >&2
    return 1
  fi
  local json ok
  json=$("$helper" --fresh) || true
  ok=$(jq -r '.ok // empty' <<<"$json" 2>/dev/null)
  if [ "$ok" != true ]; then
    echo "resume: claude usage fetch failed — not waiting on stale limits" >&2
    return 1
  fi
  jq -r '[.seven_day // 0, .resets_5h // 0, .resets_7d // 0, .fable // 0, .resets_fable // 0] | @tsv' <<<"$json"
}

_resume_schedule_wake() {
  local end="$1" when
  _resume_wake_stamp=""
  when=$(date -r "$end" '+%m/%d/%y %H:%M:%S')
  if sudo pmset schedule wake "$when"; then
    echo "resume: scheduled wake at $when" >&2
    _resume_wake_stamp=$when
    return 0
  fi
  echo "resume: --wake could not schedule a power event (pmset needs admin). The waiter will still fire when the Mac is awake." >&2
  return 1
}

# Overridden in tests. Waiter compares date +%s to an absolute end, so sleep
# does not miss the target; the session starts on wake if the Mac slept past it.
_resume_sleep_until() {
  local label="$1" clock="$2" delay="$3" shell_pid="$4" wake_when="$5"
  shift 5
  sh -c '
    label=$1; clock=$2; delay=$3; shell_pid=$4; wake_when=$5; shift 5
    end=$(( $(date +%s) + delay ))
    cancel_wake() {
      [ -n "$wake_when" ] || return 0
      sudo -n pmset schedule cancel wake "$wake_when" 2>/dev/null || true
    }
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
        if [ "$rem" -gt 60 ]; then sleep 1
        else sleep 0.1
        fi
        i=$(( i + 1 ))
      done
    ) &
    spin_pid=$!
    trap "kill $spin_pid 2>/dev/null; cancel_wake; printf \"\n\"; exit 130" INT TERM
    trap "kill $spin_pid 2>/dev/null" EXIT
    wait "$spin_pid" 2>/dev/null
    printf "\r\033[K\033]2;\007"
    export SESSION_GUARD_SHELL_PID="$shell_pid"
    exec "$@"
  ' _ "$label" "$clock" "$delay" "$shell_pid" "$wake_when" "$@"
}

_resume_last_session() {
  local tool="$1" shell_pid="$2" session_id
  if ! command -v session-guard >/dev/null 2>&1; then
    echo "resume: session-guard is required for tab-local session selection" >&2
    return 1
  fi
  session_id=$(session-guard last-session --tool "$tool" --shell-pid "$shell_pid" 2>/dev/null)
  if [ -z "$session_id" ]; then
    echo "resume: no $tool session recorded for this terminal tab; use --session ID to choose one" >&2
    return 1
  fi
  printf '%s\n' "$session_id"
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
