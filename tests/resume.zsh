#!/usr/bin/env zsh

repo_root="${0:A:h:h}"
source "$repo_root/scripts/bin/resume.sh"

test_home="$(mktemp -d "${TMPDIR:-/tmp}/resume-tests.XXXXXX")" || exit 1
trap 'rm -rf "$test_home"' EXIT
export HOME="$test_home"

mkdir -p "$HOME/.codex/sessions/2026/05/30" "$HOME/.codex/sessions/2026/05/31"
old_codex_session="11111111-1111-4111-8111-111111111111"
latest_codex_session="22222222-2222-4222-8222-222222222222"
tab_codex_session="33333333-3333-4333-8333-333333333333"
tab_claude_session="44444444-4444-4444-8444-444444444444"
tab_grok_session="55555555-5555-4555-8555-555555555555"
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
last_shell_pid=""
last_cmd=()
last_wake_end=""
caffeinate_called=0

date() {
  case "$1" in
    +%s) print -r -- 1000 ;;
    -r) print -r -- "12:00 PM" ;;
    *) command date "$@" ;;
  esac
}

caffeinate() {
  caffeinate_called=1
}

_resume_sleep_until() {
  last_label="$1"
  last_delay="$3"
  last_shell_pid="$4"
  last_cmd=("${@:6}")
}

_resume_schedule_wake() {
  last_wake_end="$1"
  print -r -- "01/01/70 00:00:00"
}

session-guard() {
  [[ "$1" == last-session && "$2" == --tool && "$4" == --shell-pid ]] || return 2
  case "$3" in
    codex) [[ -n "$tab_codex_session" ]] || return 1; print -r -- "$tab_codex_session" ;;
    claude) [[ -n "$tab_claude_session" ]] || return 1; print -r -- "$tab_claude_session" ;;
    grok) [[ -n "$tab_grok_session" ]] || return 1; print -r -- "$tab_grok_session" ;;
    *) return 2 ;;
  esac
}

run_resume() {
  last_status=0
  last_stdout=""
  last_stderr=""
  last_label=""
  last_delay=""
  last_shell_pid=""
  last_cmd=()
  last_wake_end=""
  caffeinate_called=0
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

expect_shell_pid() {
  [[ "$last_shell_pid" == "$$" ]] || fail "expected shell pid $$, got '$last_shell_pid'"
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

run_case() {
  local before="$failures"
  current_test="$1"
  shift
  "$@"
  if (( failures == before )); then
    pass
  fi
}

test_codex_tab_session_continue() {
  run_resume codex 0s
  expect_status 0
  expect_label "Resuming codex"
  expect_delay 0
  expect_shell_pid
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n33333333-3333-4333-8333-333333333333\ncontinue'
  [[ "$caffeinate_called" == 0 ]] || fail "caffeinate should not run"
}

test_codex_latest_without_time_uses_rate_limit_reset() {
  run_resume codex
  expect_status 0
  expect_label "Resuming codex"
  expect_delay 500
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n33333333-3333-4333-8333-333333333333\ncontinue'
}

test_codex_session_continue() {
  run_resume codex -s 66666666-6666-4666-8666-666666666666 0s
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n66666666-6666-4666-8666-666666666666\ncontinue'
}

test_codex_session_equals_form_with_prompt() {
  run_resume 0s codex --session=named-session "custom prompt"
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\nnamed-session\ncustom prompt'
}

test_codex_prompt_resumes_tab_session() {
  run_resume codex 0s "custom prompt"
  expect_status 0
  expect_label "Resuming codex"
  expect_cmd $'codex\nresume\n--dangerously-bypass-approvals-and-sandbox\n33333333-3333-4333-8333-333333333333\ncustom prompt'
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
  expect_cmd $'claude\n--dangerously-skip-permissions\n--resume\n44444444-4444-4444-8444-444444444444\ncontinue'
}

test_claude_prompt_resumes() {
  run_resume 0s claude "custom prompt"
  expect_status 0
  expect_label "Resuming claude"
  expect_cmd $'claude\n--dangerously-skip-permissions\n--resume\n44444444-4444-4444-8444-444444444444\ncustom prompt'
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

test_claude_fable_exhausted_waits_for_fable_reset() {
  _resume_claude_usage() { print -r -- $'88\t1500\t2000\t100\t2500'; }
  run_resume claude
  expect_status 0
  expect_label "Resuming claude"
  expect_delay 1500
}

test_claude_fable_under_waits_for_5h() {
  _resume_claude_usage() { print -r -- $'88\t1500\t2000\t50\t2500'; }
  run_resume claude
  expect_status 0
  expect_delay 500
}

test_claude_seven_day_exhausted_waits_for_7d() {
  _resume_claude_usage() { print -r -- $'100\t1500\t2000\t50\t1800'; }
  run_resume claude
  expect_status 0
  expect_delay 1000
}

test_grok_continue() {
  run_resume grok 0s
  expect_status 0
  expect_label "Resuming grok"
  expect_cmd $'grok\n--always-approve\n--resume\n55555555-5555-4555-8555-555555555555\ncontinue'
}

test_grok_prompt_resumes() {
  run_resume 0s grok "custom prompt"
  expect_status 0
  expect_label "Resuming grok"
  expect_cmd $'grok\n--always-approve\n--resume\n55555555-5555-4555-8555-555555555555\ncustom prompt'
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
  expect_cmd $'grok\n--always-approve\n--resume\n55555555-5555-4555-8555-555555555555\ncontinue'
}

test_grok_at_limit_waits_for_period_end() {
  # epoch 1500 = 1970-01-01T00:25:00Z; date mock returns now=1000 → delay 500
  write_grok_billing 100 "1970-01-01T00:25:00+00:00"
  run_resume grok
  expect_status 0
  expect_label "Resuming grok"
  expect_delay 500
  expect_cmd $'grok\n--always-approve\n--resume\n55555555-5555-4555-8555-555555555555\ncontinue'
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

test_missing_tab_session_errors() {
  local saved="$tab_codex_session"
  tab_codex_session=""
  run_resume codex 0s
  tab_codex_session="$saved"
  expect_status 1
  expect_stderr "resume: no codex session recorded for this terminal tab; use --session ID to choose one"
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

test_duration_days() {
  run_resume codex 3d --session duration-session
  expect_status 0
  expect_delay 259200
}

test_wake_schedules_for_nonzero_delay() {
  run_resume --wake codex 3000s --session duration-session
  expect_status 0
  expect_delay 3000
  [[ "$last_wake_end" == 4000 ]] || fail "expected wake end 4000, got '$last_wake_end'"
  [[ "$caffeinate_called" == 0 ]] || fail "caffeinate should not run"
}

test_wake_skipped_for_zero_delay() {
  run_resume --wake codex 0s --session duration-session
  expect_status 0
  expect_delay 0
  [[ -z "$last_wake_end" ]] || fail "expected no wake for delay 0, got '$last_wake_end'"
}

test_bare_number_rejected() {
  run_resume codex 3000
  expect_status 1
  expect_stderr "resume: bare number '3000' is ambiguous — use 3000s, 45m, 2h, 3d, or a clock time like 7p"
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

run_case "codex resumes this tab's session with continue" test_codex_tab_session_continue
run_case "codex no-time path uses latest rate-limit reset" test_codex_latest_without_time_uses_rate_limit_reset
run_case "codex explicit session resumes with continue" test_codex_session_continue
run_case "codex explicit session accepts prompt" test_codex_session_equals_form_with_prompt
run_case "codex prompt resumes this tab's session" test_codex_prompt_resumes_tab_session
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
run_case "missing tab-local session fails closed" test_missing_tab_session_errors
run_case "duration seconds are parsed" test_duration_seconds
run_case "duration minutes are parsed" test_duration_minutes
run_case "duration hours are parsed" test_duration_hours
run_case "duration days are parsed" test_duration_days
run_case "wake flag schedules for a nonzero delay" test_wake_schedules_for_nonzero_delay
run_case "wake flag is skipped for delay 0" test_wake_skipped_for_zero_delay
run_case "claude no-time waits on exhausted Fable cap" test_claude_fable_exhausted_waits_for_fable_reset
run_case "claude no-time waits on 5h when Fable is under cap" test_claude_fable_under_waits_for_5h
run_case "claude no-time waits on 7d when weekly all-models is exhausted" test_claude_seven_day_exhausted_waits_for_7d
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
