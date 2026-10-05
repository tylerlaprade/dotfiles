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
#
# Run with arguments (as `read-guard`, on PATH from scripts/bin) to whitelist
# for good. It appends to the lists below, in the dotfiles copy of this file:
#   read-guard allow <project> <repo>...  pair each repo with <project> in ASSOCIATED
#   read-guard share <repo>...            add each repo to SHARED

# Top-level repos under ~/Code that every session may read (standing rule:
# sessions consult dotfiles/scripts/bin before writing new helpers).
SHARED=("dotfiles")
# Groups of top-level repos that may read each other, one space-separated
# group per entry.
ASSOCIATED=("Fondly scrollfondly.com")
# One file per session id, holding approved repo roots one per line.
state_dir="${READ_GUARD_STATE_DIR:-/tmp/claude-read-guard}"

set -o pipefail

in_group() {
  local member
  for member in $2; do
    [[ "$1" = "$member" ]] && return 0
  done
  return 1
}

allowed_repo() {
  [[ "$project_top" = "dotfiles" ]] && return 0
  [[ "$1" = "$project_top" ]] && return 0
  local s group
  for s in "${SHARED[@]}"; do
    [[ "$1" = "$s" ]] && return 0
  done
  for group in "${ASSOCIATED[@]}"; do
    in_group "$project_top" "$group" && in_group "$1" "$group" && return 0
  done
  return 1
}

usage() {
  echo "usage: read-guard allow <project> <repo>... | read-guard share <repo>..." >&2
  exit 2
}

# add_entry <SHARED|ASSOCIATED> <entry>: append to that list's line in this
# file, through the ~/.claude symlink to the dotfiles copy. The rename leaves
# hooks running in other sessions on the old file: bash reads a script as it
# goes, so an in-place rewrite would shift what they read next.
add_entry() {
  local self tmp
  self=$(readlink -f "${BASH_SOURCE[0]}") || return 1
  tmp=$(mktemp "$self.XXXXXX") || return 1
  if awk -v name="$1" -v entry="$2" '
    !done && index($0, name "=(") == 1 {
      if (!sub(/\(\)$/, "(\"" entry "\")")) sub(/\)$/, " \"" entry "\")")
      done = 1
    }
    { print }
    END { exit !done }' "$self" > "$tmp" && chmod 755 "$tmp" && mv "$tmp" "$self"; then
    return 0
  fi
  rm -f "$tmp"
  return 1
}

whitelist() {
  local kind="$1" name repo
  shift
  case "$kind" in
    allow) [[ $# -ge 2 ]] || usage; project_top="$1"; shift ;;
    share) [[ $# -ge 1 ]] || usage; project_top="" ;;
    *) usage ;;
  esac
  for name in ${project_top:+"$project_top"} "$@"; do
    if ! [[ "$name" =~ ^[A-Za-z0-9._+-]+$ ]] || [[ "$name" = "." || "$name" = ".." ]]; then
      echo "read-guard: not a top-level directory name under ~/Code: $name" >&2
      exit 2
    fi
  done
  for repo in "$@"; do
    if allowed_repo "$repo"; then
      echo "read-guard: $repo is already allowed${project_top:+ from $project_top}"
    elif [[ "$kind" = "share" ]]; then
      add_entry SHARED "$repo" || exit 1
      SHARED+=("$repo")
      echo "read-guard: every repo may now read $repo"
    else
      add_entry ASSOCIATED "$project_top $repo" || exit 1
      ASSOCIATED+=("$project_top $repo")
      echo "read-guard: $project_top and $repo may now read each other"
    fi
  done
  exit 0
}

[[ $# -gt 0 ]] && whitelist "$@"
[[ -t 0 ]] && usage

input=$(cat) || exit 0
# Each field gets an "x" prefix so empty fields survive read's IFS collapsing.
fields=$(jq -r \
  '[.hook_event_name // "", .session_id // "", .tool_name // "",
    (.tool_input.file_path // .tool_input.path // ""), .cwd // "",
    .tool_input.command // "",
    .agent_type // "", .agent_id // "", .subagentType // ""] | map("x" + .) | @tsv' <<< "$input" 2>/dev/null)
IFS=$'\t' read -r event sid tool path cwd command agent_type agent_id subagent_type <<< "$fields"
event="${event#x}" sid="${sid#x}" tool="${tool#x}" path="${path#x}" cwd="${cwd#x}"
command="${command#x}" agent_type="${agent_type#x}" agent_id="${agent_id#x}"
subagent_type="${subagent_type#x}"

root="${CLAUDE_PROJECT_DIR:-$cwd}"
[[ -n "$root" ]] || exit 0
case "$root" in
  "$HOME/Code") exit 0 ;;
  "$HOME/Code"/*) project_rest="${root#"$HOME"/Code/}"; project_top="${project_rest%%/*}" ;;
  *) project_top="" ;;
esac

state_file="$state_dir/$sid"

approved_repo() {
  [[ -n "$sid" ]] && grep -qxF "$HOME/Code/$1" "$state_file" 2>/dev/null
}

subagent_kind() {
  if [[ -n "$subagent_type" ]]; then
    printf '%s' "$subagent_type"
  elif [[ -n "$agent_id" ]]; then
    printf '%s' "${agent_type:-unnamed}"
  fi
}

explore_subagent() {
  local kind
  kind=$(subagent_kind | tr '[:upper:]' '[:lower:]')
  case "$kind" in
    explore|explorer|codebase_investigator) return 0 ;;
    *) return 1 ;;
  esac
}

record_repo() {
  [[ -n "$sid" ]] || return
  mkdir -p "$state_dir" 2>/dev/null || return
  find "$state_dir" -type f -mtime +7 -delete 2>/dev/null
  grep -qxF "$HOME/Code/$1" "$state_file" 2>/dev/null || printf '%s\n' "$HOME/Code/$1" >> "$state_file"
}

tilde() {
  case "$1" in
    "$HOME"|"$HOME"/*) printf '%s%s' '~' "${1#"$HOME"}" ;;
    *) printf '%s' "$1" ;;
  esac
}

join() {
  local out="$1"
  shift
  while [[ $# -gt 1 ]]; do
    out="$out, $1"
    shift
  done
  [[ $# -eq 1 ]] && out="$out and $1"
  printf '%s' "$out"
}

ask() {
  local verb="$1" targets="$2" actor kind here repos reason
  shift 2
  kind=$(subagent_kind)
  actor="The agent"
  [[ -n "$kind" ]] && actor="A $kind subagent"
  case "$root" in
    "$HOME/Code"/*) here="${root#"$HOME"/Code/}" ;;
    *) here=$(tilde "$root") ;;
  esac
  repos=$(join "$@")
  if [[ $# -eq 1 ]]; then
    reason="$actor in $here wants to $verb another repo, $repos ($targets)."
  else
    reason="$actor in $here wants to $verb other repos, $repos ($targets)."
  fi
  reason="$reason Yes allows $repos for the rest of this session. To always allow, run"
  if [[ -n "$project_top" && $# -eq 1 ]]; then
    reason="$reason \`read-guard allow $project_top $1\` ($project_top and $1 read each other) or"
  elif [[ -n "$project_top" ]]; then
    reason="$reason \`read-guard allow $project_top $*\` ($project_top and each of them read each other) or"
  fi
  reason="$reason \`read-guard share $*\` (every repo reads $repos), or answer No and tell the agent which."
  jq -n --arg reason "$reason" '
    {hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
  exit 0
}

explore_subagent && exit 0

if [[ -n "$command" ]]; then
  # Bash: scan the command for paths into top-level repos under ~/Code.
  code_re="(~|\\\$HOME|$HOME)/Code/"
  repo_re="[A-Za-z0-9._+-]*[A-Za-z0-9_+-]"
  repos=$(printf '%s' "$command" | grep -oE "$code_re$repo_re" | sed 's|.*/||' | sort -u)
  [[ -n "$repos" ]] || exit 0
  need=()
  while IFS= read -r repo; do
    allowed_repo "$repo" && continue
    if [[ "$event" = "PostToolUse" ]]; then
      record_repo "$repo"
      continue
    fi
    approved_repo "$repo" && continue
    need+=("$repo")
  done <<< "$repos"
  [[ "$event" = "PostToolUse" ]] && exit 0
  [[ ${#need[@]} -eq 0 ]] && exit 0
  # The prompt names the paths reached in those repos; the raw command is noise.
  paths=$(printf '%s' "$command" \
    | grep -oE "$code_re$repo_re(/[A-Za-z0-9._+@%,:=/-]*[A-Za-z0-9_+@%=/-])?" \
    | awk '!seen[$0]++')
  shown=()
  while IFS= read -r p; do
    rest="${p#*/Code/}"
    in_group "${rest%%/*}" "${need[*]}" && shown+=('~'"/Code/$rest")
  done <<< "$paths"
  targets=$(join "${shown[@]:0:3}")
  [[ ${#shown[@]} -gt 3 ]] && targets="$targets and $(( ${#shown[@]} - 3 )) more"
  ask "run a command on" "$targets" "${need[@]}"
fi

# File tools. No path: Grep/Glob default to the session cwd, always allowed.
[[ -n "$path" ]] || exit 0
path="${path/#\~\//$HOME/}"
case "$path" in
  /*) ;;
  *)
    [[ -n "$cwd" ]] || exit 0
    path="$cwd/$path"
    ;;
esac
case "$path" in
  */../*|*/..|*/./*|*/.)
    path=$(python3 -c 'import os,sys; print(os.path.normpath(sys.argv[1]))' "$path" 2>/dev/null)
    [[ -n "$path" ]] || exit 0
    ;;
  *) ;;
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
if [[ "$event" = "PostToolUse" ]]; then
  record_repo "$path_top"
  exit 0
fi
approved_repo "$path_top" && exit 0
case "$tool" in
  Read) verb="read" ;;
  Grep) verb="search" ;;
  Glob) verb="list files in" ;;
  *) verb="use ${tool:-a tool} on" ;;
esac
target=$(tilde "$path")
ask "$verb" "$target" "$path_top"
