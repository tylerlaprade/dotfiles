#!/usr/bin/env zsh
# Tests for .claude/hooks/read-guard.sh: pipes PreToolUse/PostToolUse JSON
# fixtures into the hook and checks the decision. Run by hand:
# tests/read-guard.zsh — exit 0 for pass, 1 for fail.

repo_root="${0:A:h:h}"
hook="$repo_root/.claude/hooks/read-guard.sh"

state_dir="$(mktemp -d "${TMPDIR:-/tmp}/read-guard-tests.XXXXXX")" || exit 1
trap 'rm -rf "$state_dir"' EXIT
export READ_GUARD_STATE_DIR="$state_dir"

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

# pre <session> <cwd> <tool_input-json>
pre() {
  print -r -- '{"hook_event_name":"PreToolUse","session_id":"'"$1"'","cwd":"'"$2"'","tool_input":'"$3"'}'
}

post() {
  print -r -- '{"hook_event_name":"PostToolUse","session_id":"'"$1"'","cwd":"'"$2"'","tool_input":'"$3"',"tool_response":{"ok":true}}'
}

read_input() {
  print -r -- '{"file_path":"'"$1"'"}'
}

bash_input() {
  print -r -- '{"command":"'"$1"'"}'
}

test_in_project_read() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/dotfiles/README.md")")"
  expect_allow
}

test_foreign_repo_read() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_grep_without_path() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" '{"pattern":"foo"}')"
  expect_allow
}

test_grep_relative_path() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" '{"pattern":"foo","path":"scripts"}')"
  expect_allow
}

test_umbrella_sibling() {
  run_hook "$HOME/Code/QueenspawnGames/castle-game" \
    "$(pre s1 "$HOME/Code/QueenspawnGames/castle-game" "$(read_input "$HOME/Code/QueenspawnGames/rps/src/main.rs")")"
  expect_allow
}

test_foreign_from_umbrella() {
  run_hook "$HOME/Code/QueenspawnGames/castle-game" \
    "$(pre s1 "$HOME/Code/QueenspawnGames/castle-game" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_outside_code_dir() {
  run_hook "$HOME/Code/BrainDump" "$(pre s1 "$HOME/Code/BrainDump" "$(read_input "$HOME/.config/helix/config.toml")")"
  expect_allow
}

test_shared_dotfiles() {
  run_hook "$HOME/Code/BrainDump" "$(pre s1 "$HOME/Code/BrainDump" "$(read_input "$HOME/Code/dotfiles/scripts/bin/resume.sh")")"
  expect_allow
}

test_dot_dot_escape() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/dotfiles/../BrainDump/App.swift")")"
  expect_ask
}

test_subdir_session_own_repo() {
  run_hook "$HOME/Code/BrainDump/ios" "$(pre s1 "$HOME/Code/BrainDump/ios" "$(read_input "$HOME/Code/BrainDump/README.md")")"
  expect_allow
}

test_bash_foreign_git() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(bash_input "git -C ~/Code/BrainDump status")")"
  expect_ask
}

test_bash_own_repo() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(bash_input "git -C $HOME/Code/dotfiles status")")"
  expect_allow
}

test_bash_shared_dotfiles() {
  run_hook "$HOME/Code/QueenspawnGames" "$(pre s1 "$HOME/Code/QueenspawnGames" "$(bash_input "git -C ~/Code/dotfiles log")")"
  expect_allow
}

test_bash_umbrella_sibling() {
  run_hook "$HOME/Code/QueenspawnGames/castle-game" \
    "$(pre s1 "$HOME/Code/QueenspawnGames/castle-game" "$(bash_input "cargo test --manifest-path $HOME/Code/QueenspawnGames/rps/Cargo.toml")")"
  expect_allow
}

test_bash_dollar_home() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" '{"command":"ls $HOME/Code/swarm-forge"}')"
  expect_ask
}

test_bash_no_repo_mention() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(bash_input "cargo clippy")")"
  expect_allow
}

test_sticky_same_session() {
  run_hook "$HOME/Code/dotfiles" "$(post sticky "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
  run_hook "$HOME/Code/dotfiles" "$(pre sticky "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/Other.swift")")"
  expect_allow
}

test_sticky_covers_bash() {
  run_hook "$HOME/Code/dotfiles" "$(pre sticky "$HOME/Code/dotfiles" "$(bash_input "git -C ~/Code/BrainDump log")")"
  expect_allow
}

test_sticky_not_other_repo() {
  run_hook "$HOME/Code/dotfiles" "$(pre sticky "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/swarm-forge/src/main.rs")")"
  expect_ask
}

test_sticky_not_other_session() {
  run_hook "$HOME/Code/dotfiles" "$(pre other "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_cwd_fallback_without_env() {
  run_hook - "$(pre s9 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
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
run_case "subdir session reads own repo" test_subdir_session_own_repo
run_case "bash touching foreign repo asks" test_bash_foreign_git
run_case "bash touching own repo is allowed" test_bash_own_repo
run_case "bash touching shared dotfiles is allowed" test_bash_shared_dotfiles
run_case "bash touching umbrella sibling is allowed" test_bash_umbrella_sibling
run_case "bash with \$HOME repo reference asks" test_bash_dollar_home
run_case "bash without repo mention is allowed" test_bash_no_repo_mention
run_case "approved repo stays allowed in session" test_sticky_same_session
run_case "approval covers bash in same session" test_sticky_covers_bash
run_case "approval excludes other repos" test_sticky_not_other_repo
run_case "approval excludes other sessions" test_sticky_not_other_session
run_case "cwd fallback without env asks" test_cwd_fallback_without_env
run_case "malformed json fails open" test_malformed_json

if (( failures > 0 )); then
  print -u2 -- "$failures failure(s)"
  exit 1
fi
