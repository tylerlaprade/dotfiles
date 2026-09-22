#!/bin/zsh
set -eu

repo_root=${0:A:h:h}
fixture=$(mktemp -d /tmp/session-guard-startup-test.XXXXXX)
trap 'rm -f "$fixture/.zshrc"; rmdir "$fixture"' EXIT
startup=$(awk '/^if \[\[ \$ZSH_EVAL_CONTEXT == file / { copying=1 } copying { print } copying && /^fi$/ { exit }' "$repo_root/.zshrc")
[[ -n $startup ]]
print -rl -- 'typeset -gi _ghostty_state=${TEST_PROMPT_STATE:-0}' \
    'session-guard() { print -r -- "CALLED:$*"; }' "$startup" > "$fixture/.zshrc"

check() {
    local expected=$1
    shift
    local output count
    output=$(ZDOTDIR="$fixture" TERM_PROGRAM=ghostty "$@" </dev/null 2>/dev/null)
    count=$(print -r -- "$output" | awk '/^CALLED:shell-start$/ { count++ } END { print count+0 }')
    [[ $count == $expected ]] || { print -u2 -- "Expected $expected calls, got $count: $*"; return 1; }
}

check 1 /bin/zsh -dil
check 0 /bin/zsh -di
check 0 /bin/zsh -dl
check 0 /bin/zsh -dilc true
check 0 env TEST_PROMPT_STATE=2 /bin/zsh -dil
check 0 /bin/zsh -dfilc 'source "$ZDOTDIR/.zshrc"'
check 0 env TERM_PROGRAM=other /bin/zsh -dil
print 'Passed 7 shell-startup gate checks.'
