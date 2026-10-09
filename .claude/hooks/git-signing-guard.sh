#!/bin/bash
# PreToolUse guard for the shell tool of Claude Code, Codex and Grok: commits
# stay GPG-signed, so a command that switches signing off is denied. It reads
# the raw event with builtins only, since it runs before every shell command.
# See the commit message for what it matches and what it cannot see.

IFS= read -r -d '' input

shopt -s nocasematch

quotes=$'[\\\\"\']*'
falsy="${quotes}(false|no|off|0)([^[:alnum:]]|$)"
signing_off='--no-gpg-sign'
signing_off+="|commit\.gpgsign(=|[[:space:]]+)${falsy}"
signing_off+="|commit\.gpgsign=${quotes}([[:space:]]|$)"
signing_off+='|unset(-all)?([[:space:]]+--?[[:alnum:]-]+)*[[:space:]]+commit\.gpgsign'
signing_off+='|GIT_CONFIG_(KEY_[0-9]+|PARAMETERS)=[^[:space:]]*gpgsign'
signing_off+='|GIT_CONFIG_GLOBAL='

[[ "$input" =~ $signing_off ]] || exit 0

reason='Commits must stay GPG-signed, and this command turns signing off. If signing fails, report the exact gpg error; if gpg-agent needs a passphrase the tool cannot show, ask Tyler to unlock it (for example: echo test | gpg --clearsign) and retry.'
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
