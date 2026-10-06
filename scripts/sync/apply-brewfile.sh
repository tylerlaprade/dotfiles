#!/bin/bash

set -euo pipefail

_source="${BASH_SOURCE[0]}"
while [[ -L "$_source" ]]; do
  _source="$(readlink "$_source")"
done
DOTFILES="$(cd "$(dirname "$_source")/../.." && pwd)"
BREWFILE="$DOTFILES/Brewfile"
STAMP="$HOME/.local/state/dotfiles-sync/brewfile.sha256"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# A scheduled run has no password prompt. Fail instead of waiting on sudo,
# and do not let Homebrew update itself just because the Brewfile changed.
export NONINTERACTIVE=1
export HOMEBREW_NO_AUTO_UPDATE=1

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed. Run install.sh once on this machine." >&2
  exit 1
fi

hash=$(shasum -a 256 "$BREWFILE" | awk '{print $1}')
if [[ -f "$STAMP" ]] && [[ "$(<"$STAMP")" == "$hash" ]]; then
  exit 0
fi

echo "Applying Brewfile..."
# Bare `tap "user/repo"` lines are remote taps. Formulae from them are
# refused until trusted, and that trust is per machine.
while IFS= read -r tap; do
  [[ -z "$tap" ]] && continue
  brew tap "$tap"
  brew trust --tap "$tap"
done < <(grep -E '^tap "[^"]+"$' "$BREWFILE" | sed -E 's/^tap "([^"]+)"$/\1/' || true)

brew bundle --no-upgrade --file="$BREWFILE"
mkdir -p "$(dirname "$STAMP")"
printf '%s\n' "$hash" > "$STAMP"
