# Prompt (Pure)
fpath+=("/opt/homebrew/share/zsh/site-functions" "$HOME/.local/share/zsh/site-functions")
() {
  local completion_dirs_changed_since_dump=(${^fpath}(N/e:'[[ $REPLY -nt ~/.zcompdump ]]':))
  (( $#completion_dirs_changed_since_dump )) && rm -f ~/.zcompdump
}
autoload -Uz compinit && compinit -C
[[ $_comp_dumpfile.zwc -nt $_comp_dumpfile ]] || zcompile $_comp_dumpfile
autoload -U promptinit; promptinit
export VIRTUAL_ENV_DISABLE_PROMPT=1
if [[ -n "$HIDE_GIT_PROMPT" ]]; then
    PROMPT='%F{blue}%1~%f %F{magenta}❯%f '
else
    prompt pure
fi

export EDITOR="hx"
export GPG_TTY=$TTY

alias dot='cd "$HOME/Code/dotfiles"'
[[ "$TERM_PROGRAM" == "vscode" ]] && . "$(code --locate-shell-integration-path zsh)"

# Completions: case-insensitive. First Tab fills only the shared prefix and
# lists matches; later Tabs cycle (Shift-Tab reverse). No arrow-key menu.
# Source - https://superuser.com/a/1092328
# Posted by mpy, modified by community. See post 'Timeline' for change history
# Retrieved 2026-08-10, License - CC BY-SA 4.0
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'
bindkey '^[[Z' reverse-menu-complete # Shift-Tab

# Mac sends Ctrl+U when Cmd+Backspace is pressed
bindkey '^U' backward-kill-line

# fnm - fast node manager
eval "$(fnm env --use-on-cd)"

# bun completions
[ -s "$HOME/.bun/_bun" ] && source "$HOME/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

export PATH="$HOME/.cargo/bin:$PATH"

# opencode
export PATH="$HOME/.opencode/bin:$PATH"

# Claude: allow bypass-permissions + overage gate (requires ~/.claude/overage-gate)
claude() {
  # Shared protocol with statusline.sh (producer). Both hardcode /tmp. A
  # wrapper-local fallback dir would silently desync them.
  local _override=/tmp/claude-overage-override
  local _rf=/tmp/claude-rate-limits.json
  local _killed=/tmp/claude-overage-killed

  local _args=("$@") _resuming=false

  while true; do
    # Gate check (skip on resume — we already waited for reset)
    if [ "$_resuming" = false ] && [ -f ~/.claude/overage-gate ] && [ ! -f "$_override" ]; then
      local _threshold=${CLAUDE_OVERAGE_THRESHOLD:-95} _blocked=false
      if [ -f "$_rf" ]; then
        local _5h _7d _r5h _r7d _now
        _5h=$(jq -r '.five_hour // 0' "$_rf" 2>/dev/null)
        _7d=$(jq -r '.seven_day // 0' "$_rf" 2>/dev/null)
        _r5h=$(jq -r '.resets_5h // 0' "$_rf" 2>/dev/null)
        _r7d=$(jq -r '.resets_7d // 0' "$_rf" 2>/dev/null)
        _now=$(date +%s)
        if [ "$_r5h" -le "$_now" ] && [ "$_r7d" -le "$_now" ]; then
          rm -f "$_rf"
        elif { [ "$_5h" -ge "$_threshold" ] && [ "$_r5h" -gt "$_now" ]; } || \
             { [ "$_7d" -ge "$_threshold" ] && [ "$_r7d" -gt "$_now" ]; }; then
          _blocked=true
        fi
      fi
      if [ ! -f "$_rf" ] && [ "$_blocked" = false ]; then
        # </dev/null: don't let the probe consume stdin piped to the real invocation
        if timeout 30s command claude --dangerously-skip-permissions --model haiku --verbose \
             -p "ok" --output-format stream-json --max-turns 1 </dev/null 2>&1 \
             | grep -q '"isUsingOverage":true'; then
          _blocked=true
        fi
      fi
      if [ "$_blocked" = true ]; then
        echo "OVERAGE GATE: blocked. Override: touch $_override" >&2
        return 1
      fi
    fi

    # Run claude (with -p monitor if needed)
    local _is_print=false
    for _arg in "${_args[@]}"; do
      case "$_arg" in -p|--print) _is_print=true; break ;; esac
    done

    local _monitor=""
    if [ "$_is_print" = true ] && [ -f ~/.claude/overage-gate ] && [ ! -f "$_override" ]; then
      ( while true; do
          sleep 60
          if timeout 30s command claude --dangerously-skip-permissions --model haiku --verbose \
               -p "ok" --output-format stream-json --max-turns 1 </dev/null 2>&1 \
               | grep -q '"isUsingOverage":true'; then
            printf '%s monitor-kill\n' "$(date +%s)" >> /tmp/claude-overage-kills.log
            touch "$_killed"
            pkill claude
            break
          fi
        done ) &
      _monitor=$!
    fi

    command claude --allow-dangerously-skip-permissions "${_args[@]}"
    local _exit=$?
    [ -n "$_monitor" ] && { kill $_monitor 2>/dev/null; wait $_monitor 2>/dev/null; }

    # Not an overage kill? Normal exit.
    [ ! -f "$_killed" ] && return $_exit
    [ ! -f ~/.claude/overage-gate ] && return $_exit

    # Overage kill: find soonest reset time, sleep, then resume
    local _now _resume_at="" _r5h _r7d
    _now=$(date +%s)
    if [ -f "$_rf" ]; then
      _r5h=$(jq -r '.resets_5h // 0' "$_rf" 2>/dev/null)
      _r7d=$(jq -r '.resets_7d // 0' "$_rf" 2>/dev/null)
      for _t in $_r5h $_r7d; do
        [ "$_t" -gt "$_now" ] && { [ -z "$_resume_at" ] || [ "$_t" -lt "$_resume_at" ]; } && _resume_at=$_t
      done
    fi
    [ -z "$_resume_at" ] && return $_exit  # no reset time known, can't auto-resume

    local _delay=$(( _resume_at - _now + 30 ))
    local _reset_time=$(date -r $(( _now + _delay )) '+%-I:%M %p' 2>/dev/null)
    echo "OVERAGE GATE: Session paused. Resuming in ${_delay}s at ${_reset_time}..." >&2
    caffeinate -ims sleep "$_delay"
    rm -f "$_killed"
    _args=(-c "The overage gate paused this session at the rate limit. The limit has now reset. Continue where you left off.")
    _resuming=true
  done
}

# fableplan — the latest Fable plans, the latest Opus executes (wraps claude() above)
[[ -f ~/Code/fableplan/fableplan.sh ]] && source ~/Code/fableplan/fableplan.sh

# cwc — change workspace (condor): create workspace + start Claude
# Prefer cw.sh from current repo (works from subdirs), fall back to any condor workspace
_cw_root=$PWD
until [[ -e $_cw_root/.git || $_cw_root == / ]]; do _cw_root=${_cw_root:h}; done
[[ -e $_cw_root/.git ]] || _cw_root=
(){ (($#)) && source $1; } ${_cw_root}/scripts/cw.sh(N) ~/Code/condor*/scripts/cw.sh(N)
unset _cw_root
if (( $+functions[cw] )); then
  functions[cwc]=$functions[cw]
  unset -f cw
fi

# Rust cw wrapper — parses CW records (CD/TITLE/EXEC) from binary
if (( $+commands[cw] )); then
  eval "$(cw shell-init zsh)"
fi

# Drop-in names. ls/cat flags mostly work; top is the TUI (btm, not `bottom`).
alias ls="eza"
alias cat="bat"
alias lg="lazygit"
alias top="btm"

# Not drop-in: old name prints the new name and fails. `command du` etc. still
# reach the system binary.
du() {
  print -u2 "Use dust, not du.  dust -d 1 <path>     dust --help"
  return 2
}
find() {
  print -u2 "Use fd, not find.  fd <pattern> [path]   fd --help"
  return 2
}
ps() {
  print -u2 "Use procs, not ps.  procs <keyword>   procs --json   procs --only Command <pid>"
  return 2
}

# zsh's log builtin (watch/login records) is unused; `log` is /usr/bin/log.
disable log

# zoxide - smart cd
_cached_output zoxide-init $commands[zoxide] zoxide init zsh && eval "$REPLY"

# direnv - auto-load .envrc files
_cached_output direnv-hook $commands[direnv] direnv hook zsh && eval "$REPLY"

# Tab title: used to prefix "#PR …" via git-meta + gh-pr-lookup on every
# precmd/preexec (~50–60ms each). Disabled — we don't use PRs currently;
# Pure still sets the tab title on its own.
# _set_tab_title() {
#   local repo branch pr_num cmd="${3:-$1}"
#   local _meta
#   _meta=$(git-meta 2>/dev/null) || return
#   IFS=$'\t' read -r repo _ branch _ <<<"$_meta"
#   local pr_info
#   pr_info=$(gh-pr-lookup "$repo" "$branch" --async 2>/dev/null)
#   local pr_num="${pr_info%%	*}"
#   if [[ -n "$pr_num" ]]; then
#     local pr_title="${pr_info#*	}"
#     local prefix=""
#     [[ -n "$cmd" ]] && prefix="${cmd%% *}: "
#     printf '\e]0;%s#%s %s\a' "$prefix" "$pr_num" "$pr_title"
#   fi
# }
# autoload -Uz add-zsh-hook
# add-zsh-hook precmd _set_tab_title
# add-zsh-hook preexec _set_tab_title

# resume — delay-resume claude/codex/grok sessions
source ~/Code/dotfiles/scripts/bin/resume.sh

# Source local secrets (not in repo)
[[ -f ~/.zshrc.local ]] && source ~/.zshrc.local
source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh

_gt_yargs_completions()
{
  local reply
  local si=$IFS
  IFS=$'
' reply=($(COMP_CWORD="$((CURRENT-1))" COMP_LINE="$BUFFER" COMP_POINT="$CURSOR" gt --get-yargs-completions "${words[@]}"))
  IFS=$si
  _describe 'values' reply
}
compdef _gt_yargs_completions gt

if [[ $ZSH_EVAL_CONTEXT == file && -o login && -o interactive &&
      -z $ZSH_EXECUTION_STRING && $TERM_PROGRAM == ghostty ]] &&
    (( ${+_ghostty_state} && _ghostty_state == 0 )); then
    session-guard shell-start
fi

# Must be last — wraps zsh widgets, breaks if loaded before other plugins
# https://github.com/zsh-users/zsh-syntax-highlighting#why-must-zsh-syntax-highlightingzsh-be-sourced-at-the-end-of-the-zshrc-file
source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
