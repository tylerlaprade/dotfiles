#!/usr/bin/env zsh
# Tests for .claude/hooks/read-guard.sh: pipes PreToolUse/PostToolUse JSON
# fixtures into the hook and checks the decision. Run by hand:
# tests/read-guard.zsh — exit 0 for pass, 1 for fail.

repo_root="${0:A:h:h}"
hook="$repo_root/.claude/hooks/read-guard.sh"

state_dir="$(mktemp -d "${TMPDIR:-/tmp}/read-guard-tests.XXXXXX")" || exit 1
trap 'rm -rf "$state_dir"' EXIT
export READ_GUARD_STATE_DIR="$state_dir"
export READ_GUARD_JUDGE_LOG="$state_dir/judge.log"
export READ_GUARD_JUDGE_BIN="$state_dir/judge-stub"
export JUDGE_STUB_PROMPT="$state_dir/judge-prompt"
print -r -- '#!/bin/sh
cat > "$JUDGE_STUB_PROMPT"
printf "%s\n" "$JUDGE_STUB_ANSWER"
exit "${JUDGE_STUB_EXIT:-0}"' > "$READ_GUARD_JUDGE_BIN"
chmod 755 "$READ_GUARD_JUDGE_BIN"

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

reason() {
  print -r -- "$last_stdout" | jq -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null
}

expect_reason_has() {
  [[ "$(reason)" == *"$1"* ]] || fail "reason lacks \"$1\": $(reason)"
}

# pre <session> <cwd> <tool_input-json> [extra json fields, no braces]
pre() {
  local extra=""
  [[ -n "${4:-}" ]] && extra=",$4"
  print -r -- '{"hook_event_name":"PreToolUse","session_id":"'"$1"'","cwd":"'"$2"'","tool_input":'"$3"''"${extra}"'}'
}

post() {
  local extra=""
  [[ -n "${4:-}" ]] && extra=",$4"
  print -r -- '{"hook_event_name":"PostToolUse","session_id":"'"$1"'","cwd":"'"$2"'","tool_input":'"$3"',"tool_response":{"ok":true}'"${extra}"'}'
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
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_dotfiles_foreign_repo_read() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
}

test_dotfiles_foreign_repo_search() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" '{"pattern":"foo","path":"../BrainDump"}')"
  expect_allow
}

test_dotfiles_bash_foreign_repos() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(bash_input "cat ~/Code/BrainDump/README.md \$HOME/Code/swarm-forge/README.md $HOME/Code/flint/README.md")")"
  expect_allow
}

test_dotfiles_subdir_session() {
  run_hook "$HOME/Code/dotfiles/scripts" "$(pre s1 "$HOME/Code/dotfiles/scripts" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
}

test_dotfiles_cwd_fallback() {
  run_hook - "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
}

test_foreign_project_with_dotfiles_cwd() {
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_trusted_session_guard_reads_foreign_repo() {
  run_hook "$HOME/Code/session-guard" "$(pre s1 "$HOME/Code/session-guard" "$(bash_input "cat ~/Code/BrainDump/README.md")")"
  expect_allow
}

test_dotfiles_prefix_repo() {
  run_hook "$HOME/Code/dotfiles-copy" "$(pre s1 "$HOME/Code/dotfiles-copy" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_dotfiles_post_does_not_record_approval() {
  run_hook "$HOME/Code/dotfiles" "$(post dotfiles-post "$HOME/Code/dotfiles" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
  run_hook "$HOME/Code/dotfiles" "$(post dotfiles-post "$HOME/Code/dotfiles" "$(bash_input "git -C ~/Code/BrainDump log")")"
  expect_allow
  [[ ! -e "$state_dir/dotfiles-post" ]] || fail "dotfiles access recorded an approval"
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

test_associated_repo_read() {
  run_hook "$HOME/Code/Fondly" "$(pre s1 "$HOME/Code/Fondly" "$(read_input "$HOME/Code/scrollfondly.com/src/index.ts")")"
  expect_allow
}

test_associated_repo_reverse() {
  run_hook "$HOME/Code/scrollfondly.com" "$(pre s1 "$HOME/Code/scrollfondly.com" "$(bash_input "git -C ~/Code/Fondly log")")"
  expect_allow
}

test_associated_not_transitive_to_foreign() {
  run_hook "$HOME/Code/Fondly" "$(pre s1 "$HOME/Code/Fondly" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_dot_dot_escape() {
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" "$(read_input "$HOME/Code/flint/../BrainDump/App.swift")")"
  expect_ask
}

test_subdir_session_own_repo() {
  run_hook "$HOME/Code/BrainDump/ios" "$(pre s1 "$HOME/Code/BrainDump/ios" "$(read_input "$HOME/Code/BrainDump/README.md")")"
  expect_allow
}

test_bash_foreign_git() {
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" "$(bash_input "git -C ~/Code/BrainDump status")")"
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
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" '{"command":"ls $HOME/Code/swarm-forge"}')"
  expect_ask
}

test_bash_sentence_ending_in_shared_repo() {
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" "$(bash_input "echo the rules live in ~/Code/dotfiles.")")"
  expect_allow
}

test_bash_no_repo_mention() {
  run_hook "$HOME/Code/dotfiles" "$(pre s1 "$HOME/Code/dotfiles" "$(bash_input "cargo clippy")")"
  expect_allow
}

test_sticky_same_session() {
  run_hook "$HOME/Code/flint" "$(post sticky "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_allow
  run_hook "$HOME/Code/flint" "$(pre sticky "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/Other.swift")")"
  expect_allow
}

test_sticky_covers_bash() {
  run_hook "$HOME/Code/flint" "$(pre sticky "$HOME/Code/flint" "$(bash_input "git -C ~/Code/BrainDump log")")"
  expect_allow
}

test_sticky_not_other_repo() {
  run_hook "$HOME/Code/flint" "$(pre sticky "$HOME/Code/flint" "$(read_input "$HOME/Code/swarm-forge/src/main.rs")")"
  expect_ask
}

test_sticky_not_other_session() {
  run_hook "$HOME/Code/flint" "$(pre other "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_cwd_fallback_without_env() {
  run_hook - "$(pre s9 "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")")"
  expect_ask
}

test_malformed_json() {
  run_hook "$HOME/Code/dotfiles" '{broken'
  expect_allow
}

foreign_read() {
  read_input "$HOME/Code/BrainDump/App.swift"
}

test_explore_read_does_not_approve_main() {
  local proj="$HOME/Code/flint" fields='"agent_id":"agent-1","agent_type":"Explore"'
  run_hook "$proj" "$(pre explore-main "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  run_hook "$proj" "$(post explore-main "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  [[ ! -e "$state_dir/explore-main" ]] || fail "explore access recorded an approval"
  run_hook "$proj" "$(pre explore-main "$proj" "$(foreign_read)")"
  expect_ask
}

test_explore_bash_does_not_approve_main() {
  local proj="$HOME/Code/flint" fields='"agent_id":"agent-1","agent_type":"Explore"'
  local cmd
  cmd="$(bash_input "git -C ~/Code/BrainDump log")"
  run_hook "$proj" "$(pre explore-bash "$proj" "$cmd" "$fields")"
  expect_allow
  run_hook "$proj" "$(post explore-bash "$proj" "$cmd" "$fields")"
  expect_allow
  run_hook "$proj" "$(pre explore-bash "$proj" "$cmd")"
  expect_ask
}

test_codex_explorer_does_not_approve_main() {
  local proj="$HOME/Code/flint" fields='"agent_id":"child-1","agent_type":"explorer"'
  run_hook "$proj" "$(post codex-explorer "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  [[ ! -e "$state_dir/codex-explorer" ]] || fail "explorer access recorded an approval"
  run_hook "$proj" "$(pre codex-explorer "$proj" "$(foreign_read)")"
  expect_ask
}

test_grok_explore_does_not_approve_main() {
  local proj="$HOME/Code/flint"
  local fields='"agent_id":"grok-child","agent_type":"general-purpose","subagentType":"explore"'
  run_hook "$proj" "$(post grok-explore "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  [[ ! -e "$state_dir/grok-explore" ]] || fail "grok explore access recorded an approval"
  run_hook "$proj" "$(pre grok-explore "$proj" "$(foreign_read)")"
  expect_ask
}

test_gemini_investigator_does_not_approve_main() {
  local proj="$HOME/Code/flint" fields='"agent_id":"gem-1","agent_type":"codebase_investigator"'
  run_hook "$proj" "$(post gemini-inv "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  [[ ! -e "$state_dir/gemini-inv" ]] || fail "investigator access recorded an approval"
  run_hook "$proj" "$(pre gemini-inv "$proj" "$(foreign_read)")"
  expect_ask
}

test_main_started_as_explore_still_asks() {
  local proj="$HOME/Code/flint"
  run_hook "$proj" "$(pre main-explore "$proj" "$(foreign_read)" '"agent_type":"Explore"')"
  expect_ask
}

test_general_purpose_still_asks() {
  local proj="$HOME/Code/flint"
  run_hook "$proj" "$(pre gp "$proj" "$(foreign_read)" '"agent_id":"agent-2","agent_type":"general-purpose"')"
  expect_ask
}

test_general_purpose_approval_reaches_main() {
  local proj="$HOME/Code/flint" fields='"agent_id":"agent-2","agent_type":"general-purpose"'
  run_hook "$proj" "$(post gp-approve "$proj" "$(foreign_read)" "$fields")"
  expect_allow
  run_hook "$proj" "$(pre gp-approve "$proj" "$(foreign_read)")"
  expect_allow
}

test_reason_names_both_repos() {
  run_hook "$HOME/Code/flint" "$(pre s1 "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")" '"tool_name":"Read"')"
  expect_ask
  [[ "$(reason)" == "flint wants to read BrainDump. Yes allows it for the rest of this session. To stop asking, tell an agent to whitelist it." ]] \
    || fail "unexpected reason: $(reason)"
}

test_bash_reason_names_repos_not_command() {
  run_hook "$HOME/Code/flint" \
    "$(pre s1 "$HOME/Code/flint" "$(bash_input "git -C ~/Code/BrainDump log -5 | head; cat ~/Code/swarm-forge/src/main.rs ~/Code/dotfiles/x")")"
  expect_ask
  expect_reason_has "flint wants to run a command in BrainDump and swarm-forge. Yes allows them for the rest of this session."
  expect_reason_has "tell an agent to whitelist them."
  [[ "$(reason)" != *"git -C"* ]] || fail "reason repeats the command: $(reason)"
}

test_reason_names_subagent_and_project() {
  local proj="$HOME/Code/QueenspawnGames/castle-game"
  run_hook "$proj" "$(pre s1 "$proj" '{"pattern":"x","path":"../../BrainDump"}' '"tool_name":"Grep","agent_id":"a","agent_type":"general-purpose"')"
  expect_reason_has "A general-purpose subagent in QueenspawnGames wants to search BrainDump."
}

test_reason_outside_code_names_the_folder() {
  run_hook "$HOME/Documents" "$(pre s1 "$HOME/Documents" "$(read_input "$HOME/Code/BrainDump/App.swift")" '"tool_name":"Read"')"
  expect_ask
  expect_reason_has "The agent in ~/Documents wants to read BrainDump."
}

transcript="$state_dir/transcript.jsonl"
print -rl -- \
  '{"type":"user","message":{"role":"user","content":"Move the flint memories into the BrainDump docs."}}' \
  '{"type":"user","message":{"role":"user","content":"<task-notification><summary>helper finished</summary></task-notification>"}}' \
  '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"TOOL OUTPUT"}]}}' \
  '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ASSISTANT CHATTER"}]}}' \
  > "$transcript"

# perm <session> <cwd> <tool_input-json> <tool_name>
perm() {
  print -r -- '{"hook_event_name":"PermissionRequest","session_id":"'"$1"'","cwd":"'"$2"'","tool_name":"'"$4"'","tool_input":'"$3"',"transcript_path":"'"$transcript"'"}'
}

decision() {
  print -r -- "$last_stdout" | jq -r '.hookSpecificOutput.decision.behavior' 2>/dev/null
}

judge_foreign_read() {
  rm -f "$JUDGE_STUB_PROMPT"
  run_hook "$HOME/Code/flint" "$(perm jd "$HOME/Code/flint" "$(read_input "$HOME/Code/BrainDump/App.swift")" Read)"
}

test_judge_allow() {
  local -x JUDGE_STUB_ANSWER=$'ALLOW\nThe task names BrainDump.'
  judge_foreign_read
  [[ "$(decision)" == "allow" ]] || fail "expected allow, got: $last_stdout"
  grep -qF "ALLOW flint -> BrainDump (Read) The task names BrainDump." "$READ_GUARD_JUDGE_LOG" \
    || fail "judgment not logged: $(<"$READ_GUARD_JUDGE_LOG")"
}

test_judge_deny_tells_the_agent_why() {
  local -x JUDGE_STUB_ANSWER=$'DENY\nBrainDump has nothing to do with a flint lint fix.'
  judge_foreign_read
  [[ "$(decision)" == "deny" ]] || fail "expected deny, got: $last_stdout"
  local message
  message="$(print -r -- "$last_stdout" | jq -r '.hookSpecificOutput.decision.message')"
  [[ "$message" == *"BrainDump has nothing to do with a flint lint fix."* ]] || fail "message lacks the reason: $message"
  [[ "$message" == *"ask Tyler in your reply"* ]] || fail "message lacks the next step: $message"
}

test_judge_unsure_leaves_the_prompt() {
  local -x JUDGE_STUB_ANSWER=$'UNSURE\nThe messages do not say.'
  judge_foreign_read
  expect_allow
}

test_judge_failure_leaves_the_prompt() {
  local -x JUDGE_STUB_ANSWER="" JUDGE_STUB_EXIT=1
  judge_foreign_read
  expect_allow
  grep -qF "NONE flint -> BrainDump" "$READ_GUARD_JUDGE_LOG" || fail "failed judgment not logged"
}

test_judge_reads_lowercase_verdicts() {
  local -x JUDGE_STUB_ANSWER=$'**Allow**\nFits the task.'
  judge_foreign_read
  [[ "$(decision)" == "allow" ]] || fail "expected allow, got: $last_stdout"
}

test_judge_prompt_holds_the_task_not_tool_output() {
  local -x JUDGE_STUB_ANSWER=$'UNSURE\nx'
  judge_foreign_read
  local prompt
  prompt="$(<"$JUDGE_STUB_PROMPT")"
  [[ "$prompt" == *"flint wants to read BrainDump, outside its own repo, flint."* ]] || fail "prompt lacks the access: $prompt"
  [[ "$prompt" == *"- Move the flint memories into the BrainDump docs."* ]] || fail "prompt lacks the task: $prompt"
  [[ "$prompt" != *"helper finished"* ]] || fail "prompt includes a harness notice"
  [[ "$prompt" != *"TOOL OUTPUT"* ]] || fail "prompt includes tool output"
  [[ "$prompt" != *"ASSISTANT CHATTER"* ]] || fail "prompt includes the agent's own text"
}

test_judge_skips_allowed_access() {
  local -x JUDGE_STUB_ANSWER=$'DENY\nx'
  rm -f "$JUDGE_STUB_PROMPT"
  run_hook "$HOME/Code/flint" "$(perm jd "$HOME/Code/flint" "$(read_input "$HOME/Code/flint/main.rs")" Read)"
  expect_allow
  [[ ! -e "$JUDGE_STUB_PROMPT" ]] || fail "judged a read of the session's own repo"
}

test_judge_mode_switches() {
  local -x JUDGE_STUB_ANSWER=$'ALLOW\nx'
  "$hook" judge off >/dev/null || fail "judge off exited $?"
  grep -qxF 'JUDGE="off"' "${hook:A}" || fail "JUDGE not switched off"
  [[ -L "$hook" ]] || fail "judge off replaced the symlink"
  judge_foreign_read
  expect_allow
  [[ ! -e "$JUDGE_STUB_PROMPT" ]] || fail "judged while off"
  "$hook" judge on >/dev/null || fail "judge on exited $?"
  grep -qxF 'JUDGE="on"' "${hook:A}" || fail "JUDGE not switched on"
  "$hook" judge maybe 2>/dev/null
  [[ $? -eq 2 ]] || fail "bad judge mode was not rejected"
}

# Whitelist tests edit a copy reached through a symlink, like ~/.claude/hooks.
with_hook_copy() {
  local hook_dir="$state_dir/hook-copy"
  rm -rf "$hook_dir"
  mkdir -p "$hook_dir"
  cp "$repo_root/.claude/hooks/read-guard.sh" "$hook_dir/read-guard.sh"
  ln -s "$hook_dir/read-guard.sh" "$hook_dir/link"
  local hook="$hook_dir/link"
  "$@"
}

test_whitelist_allow() {
  "$hook" allow flint BrainDump >/dev/null || fail "allow exited $?"
  [[ -L "$hook" ]] || fail "allow replaced the symlink"
  [[ -x "${hook:A}" ]] || fail "allow left the hook not executable"
  [[ -z "$(print -l "${hook:A:h}"/read-guard.sh.*(N))" ]] || fail "allow left a temp file"
  grep -qxF 'ASSOCIATED=("Fondly scrollfondly.com" "flint BrainDump")' "${hook:A}" || fail "ASSOCIATED not extended"
  run_hook "$HOME/Code/flint" "$(pre wl "$HOME/Code/flint" "$(foreign_read)")"
  expect_allow
  run_hook "$HOME/Code/BrainDump" "$(pre wl "$HOME/Code/BrainDump" "$(read_input "$HOME/Code/flint/main.rs")")"
  expect_allow
  run_hook "$HOME/Code/BrainDump" "$(pre wl "$HOME/Code/BrainDump" "$(read_input "$HOME/Code/swarm-forge/main.rs")")"
  expect_ask
  "$hook" allow flint BrainDump >/dev/null || fail "repeat allow exited $?"
  [[ "$(grep -c '"flint BrainDump"' "${hook:A}")" -eq 1 ]] || fail "repeat allow added a duplicate"
}

test_whitelist_share() {
  "$hook" share swarm-forge >/dev/null || fail "share exited $?"
  grep -qxF 'SHARED=("dotfiles" "swarm-forge")' "${hook:A}" || fail "SHARED not extended"
  run_hook "$HOME/Code/flint" "$(pre wl "$HOME/Code/flint" "$(bash_input "ls ~/Code/swarm-forge")")"
  expect_allow
}

test_whitelist_trust() {
  "$hook" trust flint >/dev/null || fail "trust exited $?"
  grep -qxF 'TRUSTED=("dotfiles" "session-guard" "flint")' "${hook:A}" || fail "TRUSTED not extended"
  run_hook "$HOME/Code/flint" "$(pre wl "$HOME/Code/flint" "$(bash_input "ls ~/Code/swarm-forge")")"
  expect_allow
  run_hook "$HOME/Code/BrainDump" "$(pre wl "$HOME/Code/BrainDump" "$(read_input "$HOME/Code/flint/main.rs")")"
  expect_ask
  "$hook" trust flint >/dev/null || fail "repeat trust exited $?"
  [[ "$(grep -c '"flint"' "${hook:A}")" -eq 1 ]] || fail "repeat trust added a duplicate"
}

test_whitelist_rejects_bad_names() {
  local before
  before="$(<"${hook:A}")"
  "$hook" allow flint ../BrainDump 2>/dev/null
  [[ $? -eq 2 ]] || fail "bad repo name was not rejected"
  "$hook" share 2>/dev/null
  [[ $? -eq 2 ]] || fail "share without a repo was not rejected"
  "$hook" trust ../flint 2>/dev/null
  [[ $? -eq 2 ]] || fail "bad trusted name was not rejected"
  [[ "$(<"${hook:A}")" == "$before" ]] || fail "rejected whitelist changed the file"
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
run_case "dotfiles reads foreign repos" test_dotfiles_foreign_repo_read
run_case "dotfiles searches foreign repos" test_dotfiles_foreign_repo_search
run_case "dotfiles bash reads multiple foreign repos" test_dotfiles_bash_foreign_repos
run_case "dotfiles subdir session reads foreign repos" test_dotfiles_subdir_session
run_case "dotfiles cwd fallback reads foreign repos" test_dotfiles_cwd_fallback
run_case "foreign project with dotfiles cwd still asks" test_foreign_project_with_dotfiles_cwd
run_case "trusted session-guard reads foreign repos" test_trusted_session_guard_reads_foreign_repo
run_case "dotfiles prefix repo still asks" test_dotfiles_prefix_repo
run_case "dotfiles access does not record approvals" test_dotfiles_post_does_not_record_approval
run_case "grep without path is allowed" test_grep_without_path
run_case "grep relative path is allowed" test_grep_relative_path
run_case "umbrella sibling is allowed" test_umbrella_sibling
run_case "foreign read from umbrella asks" test_foreign_from_umbrella
run_case "path outside ~/Code is allowed" test_outside_code_dir
run_case "shared dotfiles repo is allowed" test_shared_dotfiles
run_case "associated repo read is allowed" test_associated_repo_read
run_case "associated repo bash in reverse is allowed" test_associated_repo_reverse
run_case "associated group excludes foreign repos" test_associated_not_transitive_to_foreign
run_case "dot-dot escape asks" test_dot_dot_escape
run_case "subdir session reads own repo" test_subdir_session_own_repo
run_case "bash touching foreign repo asks" test_bash_foreign_git
run_case "bash touching own repo is allowed" test_bash_own_repo
run_case "bash touching shared dotfiles is allowed" test_bash_shared_dotfiles
run_case "bash touching umbrella sibling is allowed" test_bash_umbrella_sibling
run_case "bash with \$HOME repo reference asks" test_bash_dollar_home
run_case "bash sentence ending in shared repo is allowed" test_bash_sentence_ending_in_shared_repo
run_case "bash without repo mention is allowed" test_bash_no_repo_mention
run_case "approved repo stays allowed in session" test_sticky_same_session
run_case "approval covers bash in same session" test_sticky_covers_bash
run_case "approval excludes other repos" test_sticky_not_other_repo
run_case "approval excludes other sessions" test_sticky_not_other_session
run_case "cwd fallback without env asks" test_cwd_fallback_without_env
run_case "malformed json fails open" test_malformed_json
run_case "explore read does not approve the main agent" test_explore_read_does_not_approve_main
run_case "explore bash does not approve the main agent" test_explore_bash_does_not_approve_main
run_case "codex explorer does not approve the main agent" test_codex_explorer_does_not_approve_main
run_case "grok explore does not approve the main agent" test_grok_explore_does_not_approve_main
run_case "gemini investigator does not approve the main agent" test_gemini_investigator_does_not_approve_main
run_case "main session started as Explore still asks" test_main_started_as_explore_still_asks
run_case "general-purpose subagent still asks" test_general_purpose_still_asks
run_case "general-purpose approval reaches the main agent" test_general_purpose_approval_reaches_main
run_case "reason names both repos" test_reason_names_both_repos
run_case "bash reason names repos, not the command" test_bash_reason_names_repos_not_command
run_case "reason names the subagent and its project" test_reason_names_subagent_and_project
run_case "reason outside ~/Code names the folder" test_reason_outside_code_names_the_folder
run_case "allow pairs repos in ASSOCIATED" with_hook_copy test_whitelist_allow
run_case "share adds a repo to SHARED" with_hook_copy test_whitelist_share
run_case "trust adds a repo to TRUSTED" with_hook_copy test_whitelist_trust
run_case "whitelist rejects bad names" with_hook_copy test_whitelist_rejects_bad_names
run_case "judge allows access that fits the task" test_judge_allow
run_case "judge refusal tells the agent why" test_judge_deny_tells_the_agent_why
run_case "unsure judge leaves the prompt" test_judge_unsure_leaves_the_prompt
run_case "failed judge leaves the prompt" test_judge_failure_leaves_the_prompt
run_case "judge reads lowercase verdicts" test_judge_reads_lowercase_verdicts
run_case "judge prompt holds the task, not tool output" test_judge_prompt_holds_the_task_not_tool_output
run_case "judge skips allowed access" test_judge_skips_allowed_access
run_case "judge mode switches on and off" with_hook_copy test_judge_mode_switches

if (( failures > 0 )); then
  print -u2 -- "$failures failure(s)"
  exit 1
fi
