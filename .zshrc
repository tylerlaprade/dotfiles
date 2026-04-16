# Prompt (Pure)
fpath+=("/opt/homebrew/share/zsh/site-functions")
autoload -Uz compinit && compinit
autoload -U promptinit; promptinit
export VIRTUAL_ENV_DISABLE_PROMPT=1
if [[ -n "$HIDE_GIT_PROMPT" ]]; then
    PROMPT='%F{blue}%1~%f %F{magenta}❯%f '
else
    prompt pure
fi

export EDITOR="hx"
export GPG_TTY=$(tty)

alias brew="wax"
alias dot='cd "$HOME/Code/dotfiles"'
[[ "$TERM_PROGRAM" == "vscode" ]] && . "$(code --locate-shell-integration-path zsh)"

# Mac sends Ctrl+U when Cmd+Backspace is pressed
bindkey '^U' backward-kill-line

# fnm - fast node manager
eval "$(fnm env --use-on-cd)"

# bun completions
[ -s "$HOME/.bun/_bun" ] && source "$HOME/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"


# Zellij dev session - uses git repo name or optional argument
zj() {
  local name="${1:-$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")}"
  if [[ -z "$name" ]]; then
    echo "Not in a git repo and no session name provided"
    return 1
  fi
  ZJ_PROJECT_DIR="$HOME/Code/$name" zellij -n ~/.config/zellij/layouts/condor.kdl -s "$name" 2>/dev/null || zellij attach "$name"
}

# Claude: allow bypass-permissions + overage gate (requires ~/.claude/overage-gate)
claude() {
  local _args=("$@") _resuming=false

  while true; do
    # Gate check (skip on resume — we already waited for reset)
    if [ "$_resuming" = false ] && [ -f ~/.claude/overage-gate ] && [ ! -f /tmp/claude-overage-override ]; then
      local _threshold=${CLAUDE_OVERAGE_THRESHOLD:-95} _blocked=false
      local _rf="/tmp/claude-rate-limits.json"
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
        if timeout 30s command claude --dangerously-skip-permissions --model haiku --verbose \
             -p "ok" --output-format stream-json --max-turns 1 2>&1 \
             | grep -q '"isUsingOverage":true'; then
          _blocked=true
        fi
      fi
      if [ "$_blocked" = true ]; then
        echo "OVERAGE GATE: blocked. Override: touch /tmp/claude-overage-override" >&2
        return 1
      fi
    fi

    # Run claude (with -p monitor if needed)
    local _is_print=false
    for _arg in "${_args[@]}"; do
      case "$_arg" in -p|--print) _is_print=true; break ;; esac
    done

    local _monitor=""
    if [ "$_is_print" = true ] && [ -f ~/.claude/overage-gate ] && [ ! -f /tmp/claude-overage-override ]; then
      ( while true; do
          sleep 60
          if timeout 30s command claude --dangerously-skip-permissions --model haiku --verbose \
               -p "ok" --output-format stream-json --max-turns 1 2>&1 \
               | grep -q '"isUsingOverage":true'; then
            printf '%s monitor-kill\n' "$(date +%s)" >> /tmp/claude-overage-kills.log
            touch /tmp/claude-overage-killed
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
    [ ! -f /tmp/claude-overage-killed ] && return $_exit
    [ ! -f ~/.claude/overage-gate ] && return $_exit

    # Overage kill: find soonest reset time, sleep, then resume
    local _now _resume_at="" _r5h _r7d
    _now=$(date +%s)
    if [ -f /tmp/claude-rate-limits.json ]; then
      _r5h=$(jq -r '.resets_5h // 0' /tmp/claude-rate-limits.json 2>/dev/null)
      _r7d=$(jq -r '.resets_7d // 0' /tmp/claude-rate-limits.json 2>/dev/null)
      for _t in $_r5h $_r7d; do
        [ "$_t" -gt "$_now" ] && { [ -z "$_resume_at" ] || [ "$_t" -lt "$_resume_at" ]; } && _resume_at=$_t
      done
    fi
    [ -z "$_resume_at" ] && return $_exit  # no reset time known, can't auto-resume

    local _delay=$(( _resume_at - _now + 30 ))
    local _reset_time=$(date -r $(( _now + _delay )) '+%-I:%M %p' 2>/dev/null)
    echo "OVERAGE GATE: Session paused. Resuming in ${_delay}s at ${_reset_time}..." >&2
    caffeinate -ims sleep "$_delay"
    rm -f /tmp/claude-overage-killed
    _args=(-c "The overage gate paused this session at the rate limit. The limit has now reset. Continue where you left off.")
    _resuming=true
  done
}

# cw — condor workspace: create workspace + start Claude
# Prefer cw.sh from current repo (works from subdirs), fall back to any condor workspace
_cw_root=$(git rev-parse --show-toplevel 2>/dev/null)
(){ (($#)) && source $1; } ${_cw_root}/scripts/cw.sh(N) ~/Code/condor*/scripts/cw.sh(N)
unset _cw_root

# Rust tool aliases
alias ls="eza"
alias cat="bat"
alias find="fd"
alias du="dust"
alias lg="lazygit"
alias top="bottom"
alias ps="procs"

# zoxide - smart cd
eval "$(zoxide init zsh)"

# direnv - auto-load .envrc files
eval "$(direnv hook zsh)"

# Tab title: prefix "#PR" when a PR exists, otherwise let Pure handle it
# (Pure's precmd sets "%~", preexec sets "<dir>: <cmd>" — so TUIs like hx
# show as "dotfiles: hx foo.rs" instead of cwd path).
_set_tab_title() {
  local repo branch pr_num
  repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null) || return
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return
  local pr_info
  pr_info=$(gh-pr-lookup "$repo" "$branch" --async 2>/dev/null)
  local pr_num="${pr_info%%	*}"
  if [[ -n "$pr_num" ]]; then
    local pr_title="${pr_info#*	}"
    printf '\e]0;#%s %s\a' "$pr_num" "$pr_title"
  fi
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _set_tab_title
add-zsh-hook preexec _set_tab_title

# resume — delay-resume claude/codex sessions
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

# Must be last — wraps zsh widgets, breaks if loaded before other plugins
# https://github.com/zsh-users/zsh-syntax-highlighting#why-must-zsh-syntax-highlightingzsh-be-sourced-at-the-end-of-the-zshrc-file
source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

