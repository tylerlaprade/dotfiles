# Amazon Q pre block. Keep at the top of this file.
[[ -f "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"
source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
PS1="%F{white}%K{blue}%1~ %%%f%k "

. "$HOME/.local/bin/env"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"                   # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion" # This loads nvm bash_completion

# bun completions
[ -s "/Users/tyler/.bun/_bun" ] && source "/Users/tyler/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
PS1="%F{white}%K{blue}%1~ %%%f%k "
export PATH=$HOME/.local/bin:/Users/tyler/.opencode/bin:$PATH

# Amazon Q post block. Keep at the bottom of this file.
[[ -f "${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh" ]] && builtin source "${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh"
