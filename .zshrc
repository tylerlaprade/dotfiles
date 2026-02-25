source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

# Prompt (Pure)
fpath+=("/opt/homebrew/share/zsh/site-functions")
autoload -U promptinit; promptinit
export VIRTUAL_ENV_DISABLE_PROMPT=1
if [[ -n "$HIDE_GIT_PROMPT" ]]; then
    PROMPT='%F{blue}%1~%f %F{magenta}❯%f '
else
    prompt pure
fi

. "$HOME/.local/bin/env"
export EDITOR="hx"
[[ "$TERM_PROGRAM" == "vscode" ]] && . "$(code --locate-shell-integration-path zsh)"

# Mac sends Ctrl+U when Cmd+Backspace is pressed
bindkey '^U' backward-kill-line

# fnm - fast node manager
eval "$(fnm env --use-on-cd)"

# bun completions
[ -s "/Users/tyler/.bun/_bun" ] && source "/Users/tyler/.bun/_bun"

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
alias top="bottom"
alias ps="procs"

# zoxide - smart cd
eval "$(zoxide init zsh)"

# direnv - auto-load .envrc files
eval "$(direnv hook zsh)"

# Tab title: prefix "#PR" when a PR exists, otherwise let Ghostty default
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
  else
    printf '\e]0;\a'
  fi
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _set_tab_title
add-zsh-hook preexec _set_tab_title

# Source local secrets (not in repo)
[[ -f ~/.zshrc.local ]] && source ~/.zshrc.local
source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh
