#!/bin/bash
# PreToolUse guard for Claude Code's shell tool: several sessions share each
# working tree, so git must never set their unsaved edits aside on its own.
# The settings deny git stash by prefix; these flags sit mid-command, where a
# prefix rule cannot see them. It reads the raw event with builtins only, since
# it runs before every shell command.

IFS= read -r -d '' input

shopt -s nocasematch

quotes=$'[\\\\"\']*'
command_end='([[:space:]]|\\n|"|$)'
autostashes="switch([[:space:]][^;&|]*)?[[:space:]](-[[:alpha:]]*m[[:alpha:]]*|--merge)${command_end}"
autostashes+="|[[:space:]]--autostash${command_end}"
autostashes+="|autostash${quotes}(=|[[:space:]]+)${quotes}(true|yes|on|1)([^[:alnum:]]|$)"
autostashes+="|-c[[:space:]]+${quotes}[[:alnum:].]*autostash${quotes}${command_end}"

[[ "$input" =~ $autostashes ]] || exit 0

reason='This git command stashes the working tree behind the scenes (switch -m or --merge, --autostash, or autoStash config), which would set aside other sessions'"'"' unsaved edits in this shared tree. Bring in newer commits without it: commit your own work, then move it with git switch --detach origin/<branch> and git cherry-pick, which leave others'"'"' files alone; if a dirty file blocks the switch, report it instead.'
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
