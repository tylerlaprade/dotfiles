# Amazon Q pre block. Keep at the top of this file.
[[ -f "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"

source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

# Prompt (Pure)
fpath+=("$(brew --prefix)/share/zsh/site-functions")
autoload -U promptinit; promptinit
export VIRTUAL_ENV_DISABLE_PROMPT=1
if [[ -n "$HIDE_GIT_PROMPT" ]]; then
    PROMPT='%F{magenta}❯%f '
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

# Amazon Q post block. Keep at the bottom of this file.
[[ -f "${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh" ]] && builtin source "${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh"
# Source local secrets (not in repo)
[[ -f ~/.zshrc.local ]] && source ~/.zshrc.local
source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh
