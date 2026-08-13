#!/bin/bash
# Hook for Read|Grep|Glob|Bash: turn cross-project access into permission
# prompts, even in bypassPermissions mode. Access is cross-project when it
# targets a top-level repo under ~/Code other than the session's own.
# "Own" is the first directory under ~/Code that holds the session's project,
# so nested repos (the QueenspawnGames umbrella) share access with every
# sibling under the same top-level directory.
#
# PreToolUse: emit an ask decision for cross-project access, unless this
# session already got approval for that repo. Output nothing to allow.
# PostToolUse: cross-project access that succeeded was approved by the user,
# so record its repo in the session's state file; later access to the same
# repo in the same session then passes without a prompt.
# Fail-soft: on any parse or lookup problem, exit 0 with no opinion.

# Top-level repos under ~/Code that every session may read (standing rule:
# sessions consult dotfiles/scripts/bin before writing new helpers).
SHARED=("dotfiles")
# One file per session id, holding approved repo roots one per line.
state_dir="${READ_GUARD_STATE_DIR:-/tmp/claude-read-guard}"

input=$(cat) || exit 0
# Each field gets an "x" prefix so empty fields survive read's IFS collapsing.
IFS=$'\t' read -r event sid path cwd command < <(printf '%s' "$input" | jq -r \
  '[.hook_event_name // "", .session_id // "",
    (.tool_input.file_path // .tool_input.path // ""), .cwd // "",
    .tool_input.command // ""] | map("x" + .) | @tsv' 2>/dev/null)
event="${event#x}" sid="${sid#x}" path="${path#x}" cwd="${cwd#x}" command="${command#x}"

root="${CLAUDE_PROJECT_DIR:-$cwd}"
[ -n "$root" ] || exit 0
case "$root" in
  "$HOME/Code") exit 0 ;;
  "$HOME/Code"/*) project_rest="${root#"$HOME"/Code/}"; project_top="${project_rest%%/*}" ;;
  *) project_top="" ;;
esac

state_file="$state_dir/$sid"

allowed_repo() {
  [ "$1" = "$project_top" ] && return 0
  local s
  for s in "${SHARED[@]}"; do
    [ "$1" = "$s" ] && return 0
  done
  return 1
}

approved_repo() {
  [ -n "$sid" ] && grep -qxF "$HOME/Code/$1" "$state_file" 2>/dev/null
}

record_repo() {
  [ -n "$sid" ] || return
  mkdir -p "$state_dir" 2>/dev/null || return
  find "$state_dir" -type f -mtime +7 -delete 2>/dev/null
  grep -qxF "$HOME/Code/$1" "$state_file" 2>/dev/null || printf '%s\n' "$HOME/Code/$1" >> "$state_file"
}

ask() {
  jq -n --arg reason "Cross-project access: $1 is outside this session's project ($root). Approve to permit $2 for the rest of this session, or whitelist the repo in ~/.claude/hooks/read-guard.sh." '
    {hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
  exit 0
}

if [ -n "$command" ]; then
  # Bash: scan the command for references to top-level repos under ~/Code.
  repos=$(printf '%s' "$command" \
    | grep -oE "(~|\\\$HOME|$HOME)/Code/[A-Za-z0-9._+-]+" \
    | sed 's|.*/||' | sort -u)
  [ -n "$repos" ] || exit 0
  need=()
  while IFS= read -r repo; do
    allowed_repo "$repo" && continue
    if [ "$event" = "PostToolUse" ]; then
      record_repo "$repo"
      continue
    fi
    approved_repo "$repo" && continue
    need+=("$repo")
  done <<< "$repos"
  [ "$event" = "PostToolUse" ] && exit 0
  [ ${#need[@]} -eq 0 ] && exit 0
  ask "this command touches ${need[*]}" "that repo"
fi

# File tools. No path: Grep/Glob default to the session cwd, always allowed.
[ -n "$path" ] || exit 0
case "$path" in
  "~/"*) path="$HOME${path#\~}" ;;
esac
case "$path" in
  /*) ;;
  *)
    [ -n "$cwd" ] || exit 0
    path="$cwd/$path"
    ;;
esac
case "$path" in
  */../*|*/..|*/./*|*/.)
    path=$(python3 -c 'import os,sys; print(os.path.normpath(sys.argv[1]))' "$path" 2>/dev/null)
    [ -n "$path" ] || exit 0
    ;;
esac

# Only paths under ~/Code are guarded; the rest of the disk is not project
# territory.
case "$path" in
  "$HOME/Code"/*) ;;
  *) exit 0 ;;
esac
path_rest="${path#"$HOME"/Code/}"
path_top="${path_rest%%/*}"

allowed_repo "$path_top" && exit 0
if [ "$event" = "PostToolUse" ]; then
  record_repo "$path_top"
  exit 0
fi
approved_repo "$path_top" && exit 0
ask "$path" "reads of $HOME/Code/$path_top"
