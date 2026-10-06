#!/bin/zsh
set -u

repo_root=${0:A:h:h}
fixture=$(mktemp -d /tmp/claude-wrapper-test.XXXXXX)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/bin"

wrapper=$(awk '/^claude\(\) \{/ { copying=1 } copying { print } copying && /^}$/ { exit }' "$repo_root/.zshrc")
[[ -n $wrapper ]] || exit 1
wrapper=${wrapper//\/tmp\/claude-/$fixture/claude-}
wrapper=${wrapper//\$HOME/$fixture}
wrapper=${wrapper//\~\//$fixture/}
print -r -- "$wrapper" > "$fixture/wrapper.zsh"

for signing_command in git gpg gpg-connect-agent gpgconf; do
    print -rl -- '#!/bin/sh' \
        'printf "unexpected signing setup\n" >> "$WRAPPER_CALLS"' \
        'exit 90' > "$fixture/bin/$signing_command"
done
print -rl -- '#!/bin/sh' \
    'printf "arg:%s\n" "$@" >> "$WRAPPER_CALLS"' \
    'IFS= read -r input || input=' \
    'printf "%s\n" "$input"' \
    'exit 7' > "$fixture/bin/claude"
chmod +x "$fixture/bin/"*

run_wrapper() {
    : > "$fixture/calls"
    WRAPPER_CALLS="$fixture/calls" \
        PATH="$fixture/bin:$PATH" /bin/zsh -f -c 'source "$1"; shift; claude "$@"' \
        wrapper-test "$fixture/wrapper.zsh" "$@" <<< 'sample input' > "$fixture/stdout" 2> "$fixture/stderr"
    local result=$?
    [[ $result == 7 ]] || { print -u2 -- "Expected Claude's exit 7, got $result"; exit 1; }
    [[ $(<"$fixture/calls") != *'unexpected signing setup'* ]] || exit 1
    [[ $(<"$fixture/stdout") == 'sample input' ]] || exit 1
    [[ ! -s "$fixture/stderr" ]] || exit 1
}

run_wrapper
[[ $(<"$fixture/calls") == 'arg:--allow-dangerously-skip-permissions' ]] || exit 1

run_wrapper --resume sample-session 'sample prompt'
[[ $(<"$fixture/calls") == $'arg:--allow-dangerously-skip-permissions\narg:--resume\narg:sample-session\narg:sample prompt' ]] || exit 1

run_wrapper -p 'sample prompt'
[[ $(<"$fixture/calls") == $'arg:--allow-dangerously-skip-permissions\narg:-p\narg:sample prompt' ]] || exit 1

print 'Passed bare, resumed, and print launches without signing setup.'
