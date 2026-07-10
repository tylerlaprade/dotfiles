#!/usr/bin/env zsh

repo_root="${0:A:h:h}"
source "$repo_root/scripts/bin/resume.sh"

test_home="$(mktemp -d "${TMPDIR:-/tmp}/resume-tests.XXXXXX")" || exit 1
trap 'rm -rf "$test_home"' EXIT
export HOME="$test_home"

mkdir -p "$HOME/.codex/sessions/2026/05/30" "$HOME/.codex/sessions/2026/05/31"
old_codex_session="11111111-1111-4111-8111-111111111111"
latest_codex_session="22222222-2222-4222-8222-222222222222"
: > "$HOME/.codex/sessions/2026/05/30/rollout-2026-05-30T10-00-00-$old_codex_session.jsonl"
print -r -- '{"payload":{"rate_limits":{"secondary":{"used_percent":50,"resets_at":2000},"primary":{"resets_at":1500}}}}' \
  > "$HOME/.codex/sessions/2026/05/31/rollout-2026-05-31T10-00-00-$latest_codex_session.jsonl"

stdout_file="$test_home/stdout"
stderr_file="$test_home/stderr"
failures=0
current_test=""
last_status=0
last_stdout=""
last_stderr=""
last_label=""
last_delay=""
last_cmd=()

date() {
  case "$1" in
    +%s) print -r -- 1000 ;;
    -r) print -r -- "12:00 PM" ;;
    *) command date "$@" ;;
  esac
}

caffeinate() {
  last_label="$6"
  last_delay="$8"
  last_cmd=("${@:9}")
}

run_resume() {
  last_status=0
  last_stdout=""
  last_stderr=""
  last_label=""
  last_delay=""
  last_cmd=()
  : > "$stdout_file"
  : > "$stderr_file"
  resume "$@" > "$stdout_file" 2> "$stderr_file"
  last_status=$?
  last_stdout="$(< "$stdout_file")"
  last_stderr="$(< "$stderr_file")"
}

fail() {
  print -u2 -- "not ok - $current_test: $1"
  failures=$((failures + 1))
}

pass() {
  print -- "ok - $current_test"
}

expect_status() {
  local expected="$1"
  [[ "$last_status" == "$expected" ]] || fail "expected status $expected, got $last_status"
}

expect_stderr() {
  local expected="$1"
  [[ "$last_stderr" == "$expected" ]] || fail "expected stderr '$expected', got '$last_stderr'"
}

expect_stdout() {
  local expected="$1"
  [[ "$last_stdout" == "$expected" ]] || fail "expected stdout:\n$expected\nactual:\n$last_stdout"
}

expect_label() {
  local expected="$1"
  [[ "$last_label" == "$expected" ]] || fail "expected label '$expected', got '$last_label'"
}

expect_delay() {
  local expected="$1"
  [[ "$last_delay" == "$expected" ]] || fail "expected delay '$expected', got '$last_delay'"
}

expect_cmd() {
  local expected="$1"
  local actual
  actual="$(printf '%s\n' "${last_cmd[@]}")"
  [[ "$actual" == "$expected" ]] || fail "expected command:\n$expected\nactual:\n$actual"
}

expect_no_cmd() {
  (( ${#last_cmd[@]} == 0 )) || fail "expected no command, got ${last_cmd[*]}"
}

expected_help() {
  cat <<'EOF'
Usage: resume <codex|claude|grok> [time|duration] [options] [prompt]

Delay-launch a claude, codex, or grok session, keeping the machine awake.
Tool, time/duration, and options may be passed in any order.

No prompt arg resumes the selected/latest session with prompt "continue".
Prompt arg resumes the selected/latest session with that prompt.
Use -n/--new to start a fresh session instead of resuming.

Time/duration:
  7p, 7pm, 730p, 1220a, 5am     clock time (next occurrence)
  3000s, 45m, 2h                duration in seconds/minutes/hours
  omitted                       next rate-limit reset

Options:
  -s, --session ID_OR_NAME       resume a specific claude/codex/grok session
  -n, --new                      start a new session
  -h, --help                     show this help

Examples:
  resume claude
  resume grok
  resume codex 7p
  resume grok 7p
  resume 1220a claude
  resume codex 3000s
  resume codex -s 019... 7p
  resume 730p claude "do X"
  resume -n 730p claude "do X"
EOF
}

run_case() {
  local before="$failures"
  current_test="$1"
  shift
  "$@"
  if (( failures == before )); then
    pass
  fi
}

test_codex_latest_continue() {
  run_resume codex 0s
  expect_status 0
  expect_label "Resuming codex"
  expect_delay 0
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n22222222-2222-4222-8222-222222222222\ncontinue'
}

test_codex_latest_without_time_uses_rate_limit_reset() {
  run_resume codex
  expect_status 0
  expect_label "Resuming codex"
  expect_delay 500
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n22222222-2222-4222-8222-222222222222\ncontinue'
}

test_codex_session_continue() {
  run_resume codex -s 33333333-3333-4333-8333-333333333333 0s
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n33333333-3333-4333-8333-333333333333\ncontinue'
}

test_codex_session_equals_form_with_prompt() {
  run_resume 0s codex --session=named-session "custom prompt"
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\nnamed-session\ncustom prompt'
}

test_codex_prompt_resumes_latest() {
  run_resume codex 0s "custom prompt"
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n22222222-2222-4222-8222-222222222222\ncustom prompt'
}

test_codex_new_with_prompt() {
  run_resume -n codex 0s "new prompt"
  expect_status 0
  expect_label "Starting new codex"
  expect_cmd $'codex\n--dangerously-bypass-approvals-and-sandbox\nnew prompt'
}

test_codex_new_without_prompt() {
  run_resume --new codex 0s
  expect_status 0
  expect_label "Starting new codex"
  expect_cmd $'codex\n--dangerously-bypass-approvals-and-sandbox'
}

test_claude_continue() {
  run_resume claude 0s
  expect_status 0
  expect_label "Resuming claude"
  expect_cmd $'claude\n--dangerously-skip-permissions\n-c\ncontinue'
}

test_claude_prompt_resumes() {
  run_resume 0s claude "custom prompt"
  expect_status 0
  expect_label "Resuming claude"
  expect_cmd $'claude\n--dangerously-skip-permissions\n-c\ncustom prompt'
}

test_claude_session_with_prompt() {
  run_resume claude --session claude-session 0s "custom prompt"
  expect_status 0
  expect_label "Resuming claude"
  expect_cmd $'claude\n--dangerously-skip-permissions\n--resume\nclaude-session\ncustom prompt'
}

test_claude_new_with_prompt() {
  run_resume --new 0s claude "new prompt"
  expect_status 0
  expect_label "Starting new claude"
  expect_cmd $'claude\n--dangerously-skip-permissions\nnew prompt'
}

test_grok_continue() {
  run_resume grok 0s
  expect_status 0
  expect_label "Resuming grok"
  expect_cmd $'grok\n--always-approve\n-c\ncontinue'
}

test_grok_prompt_resumes() {
  run_resume 0s grok "custom prompt"
  expect_status 0
  expect_label "Resuming grok"
  expect_cmd $'grok\n--always-approve\n-c\ncustom prompt'
}

test_grok_session_with_prompt() {
  run_resume grok --session grok-session 0s "custom prompt"
  expect_status 0
  expect_label "Resuming grok"
  expect_cmd $'grok\n--always-approve\n--resume\ngrok-session\ncustom prompt'
}

test_grok_new_with_prompt() {
  run_resume --new 0s grok "new prompt"
  expect_status 0
  expect_label "Starting new grok"
  expect_cmd $'grok\n--always-approve\nnew prompt'
}

test_grok_new_without_prompt() {
  run_resume --new grok 0s
  expect_status 0
  expect_label "Starting new grok"
  expect_cmd $'grok\n--always-approve'
}

write_grok_billing() {
  local used_pct="$1" period_end_iso="$2"
  mkdir -p "$HOME/.grok/logs"
  print -r -- "{\"ts\":\"2026-05-31T00:00:00Z\",\"src\":\"shell\",\"msg\":\"billing: fetched credits config\",\"ctx\":{\"config\":{\"creditUsagePercent\":${used_pct},\"currentPeriod\":{\"type\":\"USAGE_PERIOD_TYPE_WEEKLY\",\"start\":\"2026-05-24T00:00:00+00:00\",\"end\":\"${period_end_iso}\"},\"billingPeriodEnd\":\"${period_end_iso}\"}}}" \
    > "$HOME/.grok/logs/unified.jsonl"
}

test_grok_under_limit_starts_now() {
  # now is mocked to 1000; under-cap credits mean delay 0
  write_grok_billing 42 "2026-05-31T00:25:00+00:00"
  run_resume grok
  expect_status 0
  expect_label "Resuming grok"
  expect_delay 0
  expect_cmd $'grok\n--always-approve\n-c\ncontinue'
}

test_grok_at_limit_waits_for_period_end() {
  # epoch 1500 = 1970-01-01T00:25:00Z; date mock returns now=1000 → delay 500
  write_grok_billing 100 "1970-01-01T00:25:00+00:00"
  run_resume grok
  expect_status 0
  expect_label "Resuming grok"
  expect_delay 500
  expect_cmd $'grok\n--always-approve\n-c\ncontinue'
}

test_grok_at_limit_stale_period_errors() {
  write_grok_billing 100 "1970-01-01T00:00:00+00:00"
  run_resume grok
  expect_status 1
  expect_stderr "resume: over credit limit (100%) but period_end=0 is not in the future — snapshot stale"
  expect_no_cmd
}

test_grok_missing_billing_log_errors() {
  rm -rf "$HOME/.grok"
  run_resume grok
  expect_status 1
  expect_stderr "resume: no grok log at $HOME/.grok/logs/unified.jsonl — run grok at least once first"
  expect_no_cmd
}

test_duration_seconds() {
  run_resume codex 3000s --session duration-session
  expect_status 0
  expect_delay 3000
}

test_duration_minutes() {
  run_resume codex 45m --session duration-session
  expect_status 0
  expect_delay 2700
}

test_duration_hours() {
  run_resume codex 2h --session duration-session
  expect_status 0
  expect_delay 7200
}

test_bare_number_rejected() {
  run_resume codex 3000
  expect_status 1
  expect_stderr "resume: bare number '3000' is ambiguous — use 3000s, 45m, 2h, or a clock time like 7p"
}

test_two_tools_rejected() {
  run_resume codex claude
  expect_status 1
  expect_stderr "resume: got two tool names; expected <codex|claude|grok> [time|duration] [--session ID] [--new] [prompt]"
}

test_two_tools_with_grok_rejected() {
  run_resume grok codex
  expect_status 1
  expect_stderr "resume: got two tool names; expected <codex|claude|grok> [time|duration] [--session ID] [--new] [prompt]"
}

test_missing_session_value_rejected() {
  run_resume codex 0s --session
  expect_status 1
  expect_stderr "resume: --session requires a session id or name"
}

test_session_and_new_rejected() {
  run_resume codex 0s --session session --new
  expect_status 1
  expect_stderr "resume: --session and --new cannot be used together"
}

test_thread_option_rejected() {
  run_resume codex 0s --thread session
  expect_status 1
  expect_stderr "resume: unknown option '--thread'"
}

test_help_long_option() {
  run_resume --help
  expect_status 0
  expect_stderr ""
  expect_stdout "$(expected_help)"
  expect_no_cmd
}

test_help_short_option_after_tool() {
  run_resume codex -h
  expect_status 0
  expect_stderr ""
  expect_stdout "$(expected_help)"
  expect_no_cmd
}

test_missing_tool_shows_help_on_stderr() {
  run_resume
  expect_status 1
  expect_stdout ""
  expect_stderr "$(expected_help)"
  expect_no_cmd
}

run_case "codex latest session resumes with continue" test_codex_latest_continue
run_case "codex no-time path uses latest rate-limit reset" test_codex_latest_without_time_uses_rate_limit_reset
run_case "codex explicit session resumes with continue" test_codex_session_continue
run_case "codex explicit session accepts prompt" test_codex_session_equals_form_with_prompt
run_case "codex prompt resumes latest instead of starting new" test_codex_prompt_resumes_latest
run_case "codex --new starts fresh with prompt" test_codex_new_with_prompt
run_case "codex --new starts fresh without prompt" test_codex_new_without_prompt
run_case "claude default resumes with continue" test_claude_continue
run_case "claude prompt resumes instead of starting new" test_claude_prompt_resumes
run_case "claude explicit session accepts prompt" test_claude_session_with_prompt
run_case "claude --new starts fresh with prompt" test_claude_new_with_prompt
run_case "grok default resumes with continue" test_grok_continue
run_case "grok prompt resumes instead of starting new" test_grok_prompt_resumes
run_case "grok explicit session accepts prompt" test_grok_session_with_prompt
run_case "grok --new starts fresh with prompt" test_grok_new_with_prompt
run_case "grok --new starts fresh without prompt" test_grok_new_without_prompt
run_case "grok under credit limit starts immediately" test_grok_under_limit_starts_now
run_case "grok at credit limit waits for period end" test_grok_at_limit_waits_for_period_end
run_case "grok at credit limit with stale period errors" test_grok_at_limit_stale_period_errors
run_case "grok missing billing log errors" test_grok_missing_billing_log_errors
run_case "duration seconds are parsed" test_duration_seconds
run_case "duration minutes are parsed" test_duration_minutes
run_case "duration hours are parsed" test_duration_hours
run_case "bare numeric time is rejected" test_bare_number_rejected
run_case "two tools are rejected" test_two_tools_rejected
run_case "two tools including grok are rejected" test_two_tools_with_grok_rejected
run_case "missing session value is rejected" test_missing_session_value_rejected
run_case "session and new are rejected together" test_session_and_new_rejected
run_case "removed thread option is rejected" test_thread_option_rejected
run_case "long help option prints help" test_help_long_option
run_case "short help option works after tool" test_help_short_option_after_tool
run_case "missing tool prints help to stderr" test_missing_tool_shows_help_on_stderr

if (( failures > 0 )); then
  print -u2 -- "$failures failure(s)"
  exit 1
fi
