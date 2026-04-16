# Prompt — mimics Pure (blue dir, magenta arrow, git branch)
_prompt_git_branch() {
  local branch
  branch=$(git symbolic-ref --short HEAD 2>/dev/null) || return
  echo " $branch"
}

if [[ -n "$HIDE_GIT_PROMPT" ]]; then
  PS1='\[\e[34m\]\W\[\e[0m\] \[\e[35m\]❯\[\e[0m\] '
else
  PS1='\[\e[34m\]\W\[\e[0m\]\[\e[90m\]$(_prompt_git_branch)\[\e[0m\] \[\e[35m\]❯\[\e[0m\] '
fi

export EDITOR="hx"
export GPG_TTY=$(tty)

alias brew="wax"
alias dot='cd "$HOME/Code/dotfiles"'

# VSCode shell integration
[[ "$TERM_PROGRAM" == "vscode" ]] && [[ -f "$(code --locate-shell-integration-path bash 2>/dev/null)" ]] && . "$(code --locate-shell-integration-path bash)"

# fnm - fast node manager
eval "$(fnm env --use-on-cd --shell bash)"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

# Zellij dev session
zj() {
  local name="${1:-$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")}"
  if [[ -z "$name" ]]; then
    echo "Not in a git repo and no session name provided"
    return 1
  fi
  ZJ_PROJECT_DIR="$HOME/Code/$name" zellij -n ~/.config/zellij/layouts/condor.kdl -s "$name" 2>/dev/null || zellij attach "$name"
}

# Claude
claude() { command claude --allow-dangerously-skip-permissions "$@"; }

# cw — condor workspace
_cw_root=$(git rev-parse --show-toplevel 2>/dev/null)
_cw_found=0
if [[ -n "$_cw_root" && -f "$_cw_root/scripts/cw.sh" ]]; then
  source "$_cw_root/scripts/cw.sh"
  _cw_found=1
fi
if [[ $_cw_found -eq 0 ]]; then
  for _cw_f in "$HOME"/Code/condor*/scripts/cw.sh; do
    [[ -f "$_cw_f" ]] && source "$_cw_f" && break
  done
fi
unset _cw_root _cw_found _cw_f

# Rust tool aliases
alias ls="eza"
alias cat="bat"
alias find="fd"
alias du="dust"
alias lg="lazygit"
alias top="bottom"
alias ps="procs"

# zoxide
eval "$(zoxide init bash)"

# direnv
eval "$(direnv hook bash)"

# Tab title
_set_tab_title() {
  local repo branch pr_info pr_num pr_title
  repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null) || return
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return
  pr_info=$(gh-pr-lookup "$repo" "$branch" --async 2>/dev/null)
  pr_num="${pr_info%%	*}"
  if [[ -n "$pr_num" ]]; then
    pr_title="${pr_info#*	}"
    printf '\e]0;#%s %s\a' "$pr_num" "$pr_title"
  else
    printf '\e]0;%s\a' "${PWD/#$HOME/\~}"
  fi
}
PROMPT_COMMAND="_set_tab_title${PROMPT_COMMAND:+;$PROMPT_COMMAND}"

# preexec (brush zsh-hooks) — no Pure in bash, so we handle cmd title manually.
# Format is "cmd: dir" (unlike zshrc where Pure hardcodes "dir: cmd").
preexec() {
  local repo branch pr_info pr_num
  repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null) || return
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return
  pr_info=$(gh-pr-lookup "$repo" "$branch" --async 2>/dev/null)
  pr_num="${pr_info%%	*}"
  if [[ -n "$pr_num" ]]; then
    printf '\e]0;#%s %s\a' "$pr_num" "${pr_info#*	}"
  else
    printf '\e]0;%s: %s\a' "${1%% *}" "${PWD##*/}"
  fi
}

# Source local secrets
[[ -f ~/.bashrc.local ]] && source ~/.bashrc.local
