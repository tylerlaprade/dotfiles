#!/usr/bin/env zsh
# Tests for .claude/hooks/git-signing-guard.sh: pipes shell-tool PreToolUse
# events in the Claude/Codex and Grok shapes into the hook and checks the
# decision. Run by hand: tests/git-signing-guard.zsh — exit 0 for pass, 1 for fail.

repo_root="${0:A:h:h}"
hook="$repo_root/.claude/hooks/git-signing-guard.sh"

failures=0

decision() {
  local out
  out="$(print -r -- "$1" | "$hook")"
  [[ -z "$out" ]] && { print allow; return; }
  print -r -- "$out" | jq -r .hookSpecificOutput.permissionDecision
}

check() {
  local expected="$1" command="$2" got event
  for event in \
    "$(jq -nc --arg c "$command" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    "$(jq -nc --arg c "$command" '{hook_event_name:"PreToolUse",toolName:"run_terminal_command",toolInput:{command:$c}}')"; do
    got="$(decision "$event")"
    [[ "$expected" == allow && "$got" == allow ]] && continue
    [[ "$expected" == deny && "$got" == deny ]] && continue
    print -u2 -- "not ok - expected $expected, got $got: $command"
    (( failures++ ))
    return
  done
  print -- "ok - $expected: $command"
}

check deny  'git -c commit.gpgsign=false commit -m x'
check deny  'git -c commit.gpgSign=FALSE commit -m x'
check deny  'git -c "commit.gpgsign=false" commit -m x'
check deny  'git -c commit.gpgsign=0 commit -m x'
check deny  'git -c commit.gpgsign= commit -m x'
check deny  'git -c commit.gpgsign="" commit -m x'
check deny  'git commit --no-gpg-sign -m x'
check deny  'git rebase --no-gpg-sign main'
check deny  'cd repo && git config commit.gpgsign false'
check deny  'git config --local commit.gpgsign no'
check deny  'git config set commit.gpgsign off'
check deny  'git config --global --unset commit.gpgsign'
check deny  'git config --unset --local commit.gpgsign'
check deny  'git config unset commit.gpgsign'
check deny  'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false git commit -m x'
check deny  "GIT_CONFIG_PARAMETERS=\"'commit.gpgsign'='false'\" git commit -m x"
check deny  'GIT_CONFIG_GLOBAL=/dev/null git commit -m x'
check deny  $'git add a\ngit -c commit.gpgsign=false commit -m x'

check allow 'git commit -m "Fix the feed"'
check allow 'git commit --amend --no-edit -S'
check allow 'git -c commit.gpgsign=true commit -m x'
check allow 'git -c "commit.gpgsign=true" commit -m x'
check allow 'git config --get commit.gpgsign'
check allow 'git config commit.gpgsign && echo set'
check allow 'git config commit.gpgsign'
check allow 'git log --format=%G? -5'
check allow 'git config --unset user.name'
check allow 'git -c core.pager=cat log'

(( failures == 0 )) || exit 1
