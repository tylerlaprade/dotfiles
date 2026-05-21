. "$HOME/.cargo/env"
[[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"
[[ -d "$HOME/Code/dotfiles/scripts/bin" ]] && export PATH="$HOME/Code/dotfiles/scripts/bin:$PATH"
[[ -f "$HOME/.zshenv.local" ]] && source "$HOME/.zshenv.local"
