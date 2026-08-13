#!/usr/bin/env zsh
# Tests for .claude/hooks/read-guard.sh: pipes PreToolUse JSON fixtures into
# the hook and checks the decision. Run by hand: tests/read-guard.zsh
# Exit 0 for pass, 1 for fail.

repo_root="${0:A:h:h}"
hook="$repo_root/.claude/hooks/read-guard.sh"

failures=0
current_test=""

fail() {
  print -u2 -- "not ok - $current_test: $1"
  (( failures++ ))
}

pass() {
  print -- "ok - $current_test"
}

last_status=0
last_stdout=""

# run_hook <project-dir-or-"-" for unset> <json>
run_hook() {
  local proj="$1" json="$2"
  if [[ "$proj" == "-" ]]; then
    last_stdout="$(print -r -- "$json" | env -u CLAUDE_PROJECT_DIR "$hook" 2>/dev/null)"
  else
    last_stdout="$(print -r -- "$json" | CLAUDE_PROJECT_DIR="$proj" "$hook" 2>/dev/null)"
  fi
  last_status=$?
}

expect_status() {
  [[ "$last_status" -eq "$1" ]] || fail "status $last_status, expected $1"
}

expect_allow() {
  expect_status 0
  [[ -z "$last_stdout" ]] || fail "expected no output, got: $last_stdout"
}

expect_ask() {
  expect_status 0
  local decision
  decision="$(print -r -- "$last_stdout" | jq -r '.hookSpecificOutput.permissionDecision' 2>/dev/null)"
  [[ "$decision" == "ask" ]] || fail "expected ask decision, got: $last_stdout"
}

read_json() {
  print -r -- '{"cwd":"'"$2"'","tool_name":"Read","tool_input":{"file_path":"'"$1"'"}}'
}

test_in_project_read() {
  run_hook "$HOME/Code/dotfiles" "$(read_json "$HOME/Code/dotfiles/README.md" "$HOME/Code/dotfiles")"
  expect_allow
}

test_foreign_repo_read() {
  run_hook "$HOME/Code/dotfiles" "$(read_json "$HOME/Code/BrainDump/App.swift" "$HOME/Code/dotfiles")"
  expect_ask
}

test_grep_without_path() {
  run_hook "$HOME/Code/dotfiles" '{"cwd":"'"$HOME"'/Code/dotfiles","tool_name":"Grep","tool_input":{"pattern":"foo"}}'
  expect_allow
}

test_grep_relative_path() {
  run_hook "$HOME/Code/dotfiles" '{"cwd":"'"$HOME"'/Code/dotfiles","tool_name":"Grep","tool_input":{"pattern":"foo","path":"scripts"}}'
  expect_allow
}

test_umbrella_sibling() {
  run_hook "$HOME/Code/QueenspawnGames/castle-game" \
    "$(read_json "$HOME/Code/QueenspawnGames/rps/src/main.rs" "$HOME/Code/QueenspawnGames/castle-game")"
  expect_allow
}

test_foreign_from_umbrella() {
  run_hook "$HOME/Code/QueenspawnGames/castle-game" \
    "$(read_json "$HOME/Code/BrainDump/App.swift" "$HOME/Code/QueenspawnGames/castle-game")"
  expect_ask
}

test_outside_code_dir() {
  run_hook "$HOME/Code/BrainDump" "$(read_json "$HOME/.config/helix/config.toml" "$HOME/Code/BrainDump")"
  expect_allow
}

test_shared_dotfiles() {
  run_hook "$HOME/Code/BrainDump" "$(read_json "$HOME/Code/dotfiles/scripts/bin/resume.sh" "$HOME/Code/BrainDump")"
  expect_allow
}

test_dot_dot_escape() {
  run_hook "$HOME/Code/dotfiles" "$(read_json "$HOME/Code/dotfiles/../BrainDump/App.swift" "$HOME/Code/dotfiles")"
  expect_ask
}

test_glob_foreign_dir() {
  run_hook "$HOME/Code/dotfiles" '{"cwd":"'"$HOME"'/Code/dotfiles","tool_name":"Glob","tool_input":{"pattern":"**/*.rs","path":"'"$HOME"'/Code/swarm-forge"}}'
  expect_ask
}

test_cwd_fallback_without_env() {
  run_hook - "$(read_json "$HOME/Code/BrainDump/App.swift" "$HOME/Code/dotfiles")"
  expect_ask
}

test_malformed_json() {
  run_hook "$HOME/Code/dotfiles" '{broken'
  expect_allow
}

run_case() {
  local before=$failures
  current_test="$1"
  shift
  "$@"
  (( failures == before )) && pass
}

run_case "in-project read is allowed" test_in_project_read
run_case "foreign repo read asks" test_foreign_repo_read
run_case "grep without path is allowed" test_grep_without_path
run_case "grep relative path is allowed" test_grep_relative_path
run_case "umbrella sibling is allowed" test_umbrella_sibling
run_case "foreign read from umbrella asks" test_foreign_from_umbrella
run_case "path outside ~/Code is allowed" test_outside_code_dir
run_case "shared dotfiles repo is allowed" test_shared_dotfiles
run_case "dot-dot escape asks" test_dot_dot_escape
run_case "glob of foreign dir asks" test_glob_foreign_dir
run_case "cwd fallback without env asks" test_cwd_fallback_without_env
run_case "malformed json fails open" test_malformed_json

if (( failures > 0 )); then
  print -u2 -- "$failures failure(s)"
  exit 1
fi
