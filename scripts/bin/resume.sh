# resume — delay-resume a claude or codex session, keeping the machine awake
# Source from .zshrc / .bashrc:  source ~/Code/dotfiles/scripts/bin/resume.sh
#
# Usage:
#   resume codex 7p              # resume codex at 7:00 PM, prompt "continue"
#   resume claude 1220a          # resume claude at 12:20 AM
#   resume codex 2760            # resume codex in 2760 seconds
#   resume claude 730p "do X"    # resume claude at 7:30 PM with custom prompt

resume() {
  local tool="$1" time_str="$2"
  shift 2

  local delay

  if [[ $time_str == *[apAP] ]]; then
    local ampm="${time_str#"${time_str%?}"}"
    local time_num="${time_str%[apAP]}"
    local hour min

    if [ ${#time_num} -le 2 ]; then
      hour=$time_num min=0
    else
      min=${time_num#"${time_num%??}"}
      hour=${time_num%??}
    fi

    case $ampm in
      [pP]) [ "$hour" -ne 12 ] && hour=$((hour + 12)) ;;
      [aA]) [ "$hour" -eq 12 ] && hour=0 ;;
    esac

    local target_ts now_ts
    target_ts=$(date -j -f "%H:%M:%S" "$(printf '%02d:%02d:00' "$hour" "$min")" +%s)
    now_ts=$(date +%s)
    delay=$((target_ts - now_ts))
    [ "$delay" -le 0 ] && delay=$((delay + 86400))
  else
    delay=$time_str
  fi

  local prompt="${1:-continue}"
  echo "Resuming $tool in ${delay}s ($(date -r $(($(date +%s) + delay)) '+%I:%M %p'))"

  case "$tool" in
    codex)
      caffeinate -ims sh -c 'sleep "$1" && codex resume --last --dangerously-bypass-approvals-and-sandbox "$2"' _ "$delay" "$prompt" ;;
    claude)
      caffeinate -ims sh -c 'sleep "$1" && claude --dangerously-skip-permissions -c "$2"' _ "$delay" "$prompt" ;;
    *)
      echo "Usage: resume <codex|claude> <time|seconds> [prompt]" >&2
      return 1 ;;
  esac
}
