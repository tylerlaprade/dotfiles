eval "$(/opt/homebrew/bin/brew shellenv)"
. "$HOME/.cargo/env"
[[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"
