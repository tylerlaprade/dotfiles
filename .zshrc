# Amazon Q pre block. Keep at the top of this file.
[[ -f "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"

source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

# Prompt
export VIRTUAL_ENV_DISABLE_PROMPT=1
PS1="%F{black}%K{green}%1~ %%%f%k "

. "$HOME/.local/bin/env"
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
# GITHUB_PERSONAL_ACCESS_TOKEN - stored securely, not in dotfiles
source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh
