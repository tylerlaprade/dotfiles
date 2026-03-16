#!/bin/bash
set -e

# One-liner: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tylerlaprade/dotfiles/master/bootstrap.sh)"

DOTFILES_DIR="$HOME/Code/dotfiles"
REPO="https://github.com/tylerlaprade/dotfiles.git"

# Homebrew (also installs Xcode CLT, which provides git)
if ! command -v brew &>/dev/null; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# Clone dotfiles
if [ ! -d "$DOTFILES_DIR" ]; then
  echo "Cloning dotfiles..."
  mkdir -p "$(dirname "$DOTFILES_DIR")"
  git clone "$REPO" "$DOTFILES_DIR"
else
  echo "Dotfiles already cloned at $DOTFILES_DIR"
fi

# Run install
exec "$DOTFILES_DIR/install.sh"
