#!/bin/bash
# PreToolUse hook (Read|Grep|Glob): turn cross-project reads into permission
# prompts, even in bypassPermissions mode. A read is cross-project when it
# targets ~/Code but lands outside the session's project, outside SHARED
# repos, and outside the project's umbrella (for repos nested under one).
# Input: hook JSON on stdin. Output: ask-decision JSON, or nothing to allow.
# Fail-soft: on any parse or lookup problem, exit 0 with no opinion.

# Repos nested under these roots share reads with every sibling under the
# same root.
UMBRELLAS=("$HOME/Code/QueenspawnGames")
# Repos every session may read (standing rule: sessions consult
# dotfiles/scripts/bin before writing new helpers).
SHARED=("$HOME/Code/dotfiles")

input=$(cat) || exit 0
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
# No path: Grep/Glob default to the session cwd, which is always allowed.
[ -n "$path" ] || exit 0

case "$path" in
  "~/"*) path="$HOME${path#\~}" ;;
esac
case "$path" in
  /*) ;;
  *)
    cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
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

root="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)}"
[ -n "$root" ] || exit 0
for umbrella in "${UMBRELLAS[@]}"; do
  case "$root" in
    "$umbrella"|"$umbrella"/*) root="$umbrella" ;;
  esac
done

case "$path" in
  "$root"|"$root"/*) exit 0 ;;
esac
for shared in "${SHARED[@]}"; do
  case "$path" in
    "$shared"|"$shared"/*) exit 0 ;;
  esac
done

jq -n --arg reason "Cross-project read: $path is outside this session's project ($root). Approve to permit it, or whitelist the directory in ~/.claude/hooks/read-guard.sh." '
  {hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
exit 0
