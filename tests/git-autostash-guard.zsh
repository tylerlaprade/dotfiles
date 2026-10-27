#!/usr/bin/env zsh
# Tests for .claude/hooks/git-autostash-guard.sh: pipes Claude Code shell-tool
# PreToolUse events into the hook and checks the decision. Run by hand:
# tests/git-autostash-guard.zsh — exit 0 for pass, 1 for fail.

repo_root="${0:A:h:h}"
hook="$repo_root/.claude/hooks/git-autostash-guard.sh"

failures=0

check() {
  local expected="$1" command="$2" out got
  out="$(jq -nc --arg c "$command" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}' | "$hook")"
  got=allow
  [[ -n "$out" ]] && got="$(print -r -- "$out" | jq -r .hookSpecificOutput.permissionDecision)"
  if [[ "$got" != "$expected" ]]; then
    print -u2 -- "not ok - expected $expected, got $got: $command"
    (( failures++ ))
    return
  fi
  print -- "ok - $expected: $command"
}

check deny  'git switch -m main'
check deny  'git switch -q -m --detach origin/master'
check deny  'git switch -qm main'
check deny  'git switch --merge main'
check deny  'cd repo && git switch -m main && git log'
check deny  'git -C repo switch --detach -m origin/master'
check deny  'git rebase --autostash origin/master'
check deny  'git pull --rebase --autostash'
check deny  'git merge --autostash origin/master'
check deny  'git -c rebase.autoStash=true rebase origin/master'
check deny  'git -c "rebase.autostash=true" pull --rebase'
check deny  'git -c rebase.autostash rebase origin/master'
check deny  'git config rebase.autoStash true'
check deny  'git config set merge.autostash yes'
check deny  $'git add a\ngit switch -m main'
check deny  $'git switch main -m\ngit log'
check allow 'git switch main'
check allow 'git switch -c fix-merge-m'
check allow 'git switch -c feature-m'
check allow 'git switch --detach origin/master'
check allow 'git rebase --no-autostash origin/master'
check allow 'git config rebase.autostash'
check allow 'git -c rebase.autoStash=false rebase origin/master'
check allow 'git commit -m "switch sides"'
check allow 'git cherry-pick e92d04ec'
check allow 'git merge --ff-only origin/master'

(( failures == 0 ))
