#!/bin/bash
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ${1:-} == --kid-trackpad-prototype ]]; then
  prototype_python=/opt/homebrew/bin/python3
  if ! "$prototype_python" -c 'import sys; sys.exit(sys.version_info < (3, 14))' 2>/dev/null; then
    brew install python
  fi
  exec "$prototype_python" "$DOTFILES/scripts/kid-trackpad/build.py"
fi

echo "=== Dotfiles Setup ==="

sudo -v
while true; do sudo -n true; sleep 50; kill -0 "$$" || exit; done 2>/dev/null &

sudo sh -c 'echo "Defaults !tty_tickets" > /etc/sudoers.d/dotfiles-install'

failed=0
freeze_shell=0
freeze_shell_configs() {
  [[ $freeze_shell -eq 1 ]] && return 0
  local f
  for f in "$HOME/.zshrc" "$HOME/.zshenv" "$HOME/.zprofile"; do
    [[ -e "$f" ]] && chmod a-w "$f" 2>/dev/null || true
  done
  freeze_shell=1
}
thaw_shell_configs() {
  [[ $freeze_shell -eq 0 ]] && return 0
  local f
  for f in "$HOME/.zshrc" "$HOME/.zshenv" "$HOME/.zprofile"; do
    [[ -e "$f" ]] && chmod u+w "$f" 2>/dev/null || true
  done
}
cleanup() {
  thaw_shell_configs
  sudo rm -f /etc/sudoers.d/dotfiles-install
}
trap cleanup EXIT

if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew..."
  homebrew_installer=$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)
  /bin/bash -c "$homebrew_installer"
  homebrew_env=$(/opt/homebrew/bin/brew shellenv)
  eval "$homebrew_env"
fi

export PATH="$HOME/.bun/bin:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# One-time on a new Mac. A later install of one of these on one machine should stay.
for app in GarageBand iMovie Keynote Numbers Pages; do
  [[ -d "/Applications/$app.app" ]] && sudo rm -rf "/Applications/$app.app"
done

# A hostname that flips breaks GPG's stale-lock reclamation (a dead-process lock
# is only auto-broken when its recorded hostname matches the current one).
if ! scutil --get HostName &>/dev/null; then
  local_host_name=$(scutil --get LocalHostName)
  sudo scutil --set HostName "$local_host_name"
fi

"$DOTFILES/scripts/sync/apply-brewfile.sh" || failed=1

if ! command -v rustup >/dev/null 2>&1; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal
fi
if command -v rustup >/dev/null 2>&1; then
  rustup set profile minimal >/dev/null
  installed_components=$(rustup component list --installed 2>/dev/null || true)
  missing_components=()
  for component in rust-analyzer rustfmt clippy; do
    grep -q "^${component}" <<<"$installed_components" || missing_components+=("$component")
  done
  if [[ ${#missing_components[@]} -gt 0 ]]; then
    echo "Adding Rust components: ${missing_components[*]}"
    rustup component add "${missing_components[@]}" || failed=1
  fi
  mkdir -p "$HOME/.local/share/zsh/site-functions"
  rustup completions zsh cargo >"$HOME/.local/share/zsh/site-functions/_cargo"
fi

binstall_crates=(
  apple-codesign
  bacon
  cargo-insta
  cargo-update
  cargo-workspaces
  codebook-lsp
  commit-fix
  genemichaels
)
if command -v cargo >/dev/null 2>&1; then
  installed_crates=$(cargo install --list 2>/dev/null | awk '{print $1}')
  if ! grep -qxF cargo-binstall <<<"$installed_crates"; then
    echo "Installing cargo-binstall..."
    cargo install cargo-binstall || failed=1
    installed_crates+=$'\ncargo-binstall'
  fi
  missing_binstall=()
  for crate in "${binstall_crates[@]}"; do
    grep -qxF "$crate" <<<"$installed_crates" || missing_binstall+=("$crate")
  done
  if [[ ${#missing_binstall[@]} -gt 0 ]] && command -v cargo-binstall >/dev/null 2>&1; then
    echo "Installing cargo crates: ${missing_binstall[*]}"
    cargo binstall -y "${missing_binstall[@]}" || failed=1
  fi
  if ! grep -qxF session-guard <<<"$installed_crates"; then
    echo "Installing session-guard..."
    if cargo install --git https://github.com/tylerlaprade/session-guard; then
      session-guard install --terminal ghostty || failed=1
    else
      failed=1
    fi
  fi
  if ! grep -qxF lint-staged-rs <<<"$installed_crates"; then
    echo "Installing lint-staged-rs..."
    cargo install --git https://github.com/tylerlaprade/lint-staged-rs || failed=1
  fi
fi

if ! command -v hx >/dev/null 2>&1; then
  echo "Installing Helix..."
  "$DOTFILES/scripts/sync/update-helix.sh" || failed=1
fi

theme_link="$HOME/.config/helix/themes/quiet_light.toml"
if [[ ! -e "$theme_link" ]]; then
  theme_repo="$HOME/Code/helix-quiet-light-theme"
  if [[ ! -f "$theme_repo/quiet_light.toml" ]]; then
    echo "Installing Quiet Light theme..."
    mkdir -p "$(dirname "$theme_repo")"
    git clone git@github.com:tylerlaprade/helix-quiet-light-theme.git "$theme_repo" \
      || git clone https://github.com/tylerlaprade/helix-quiet-light-theme.git "$theme_repo" \
      || failed=1
  fi
  if [[ -f "$theme_repo/quiet_light.toml" ]]; then
    mkdir -p "$(dirname "$theme_link")"
    ln -sf "$theme_repo/quiet_light.toml" "$theme_link"
  fi
fi

if ! command -v bun >/dev/null 2>&1; then
  echo "Installing Bun..."
  freeze_shell_configs
  curl -fsSL https://bun.sh/install | bash || failed=1
fi

if ! command -v sourcery >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
  echo "Installing Sourcery..."
  uv tool install sourcery || failed=1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "Installing Claude Code..."
  freeze_shell_configs
  curl -fsSL https://claude.ai/install.sh | bash || failed=1
fi

if ! command -v grok >/dev/null 2>&1; then
  echo "Installing Grok..."
  freeze_shell_configs
  curl -fsSL https://x.ai/cli/install.sh | bash || failed=1
fi

if command -v fnm >/dev/null 2>&1; then
  eval "$(fnm env)" 2>/dev/null || true
  if ! fnm list 2>/dev/null | grep -q default; then
    echo "Installing Node LTS..."
    fnm install --lts && fnm default lts-latest || failed=1
  fi
fi

if ! command -v codex >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  echo "Installing Codex CLI..."
  npm i -g @openai/codex || failed=1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI auth required for app downloads..."
  gh auth login
fi

if [[ ! -d /Applications/Graphite.app ]] && gh auth status >/dev/null 2>&1; then
  echo "Installing Graphite..."
  if gh release download --repo withgraphite/graphite-desktop --pattern '*darwin-arm64*' -D /tmp --clobber; then
    unzip -qo /tmp/Graphite-darwin-arm64-*.zip -d /Applications/
    rm -f /tmp/Graphite-darwin-arm64-*.zip
  else
    failed=1
  fi
fi

while IFS= read -r entry || [[ -n "$entry" ]]; do
  [[ -z "$entry" ]] && continue
  grep -qxF "$entry" /etc/hosts || echo "$entry" | sudo tee -a /etc/hosts >/dev/null
done < "$DOTFILES/scripts/setup/hosts"

kanata_src=/opt/homebrew/bin/kanata
kanata_dest="$HOME/.local/bin/kanata"
kanata_plist_dest=/Library/LaunchDaemons/com.tylerlaprade.kanata.plist
if [[ -x "$kanata_src" ]]; then
  kanata_changed=0
  [[ -x "$kanata_dest" ]] && cmp -s "$kanata_src" "$kanata_dest" || kanata_changed=1
  rendered=$(mktemp)
  sed "s|__HOME__|$HOME|g" "$DOTFILES/LaunchDaemons/com.tylerlaprade.kanata.plist" > "$rendered"
  [[ -f "$kanata_plist_dest" ]] && cmp -s "$rendered" "$kanata_plist_dest" || kanata_changed=1
  if [[ $kanata_changed -eq 1 ]]; then
    mkdir -p "$HOME/.local/bin"
    cp "$kanata_src" "$kanata_dest"
    sudo mkdir -p /usr/local/var/log
    sudo cp "$rendered" "$kanata_plist_dest"
    sudo launchctl bootout system "$kanata_plist_dest" 2>/dev/null || true
    sudo launchctl bootstrap system "$kanata_plist_dest" || failed=1
  fi
  rm -f "$rendered"
  /opt/homebrew/bin/python3 "$DOTFILES/scripts/kid-trackpad/build.py" || failed=1
fi

if [[ -f "$HOME/.npmrc" && ! -L "$HOME/.npmrc" ]]; then
  rm "$HOME/.npmrc"
fi
for f in gpg.conf gpg-agent.conf dirmngr.conf; do
  [[ -f "$HOME/.gnupg/$f" && ! -L "$HOME/.gnupg/$f" ]] && rm "$HOME/.gnupg/$f"
done

echo "Syncing dotfiles..."
SKIP_DEFAULTS_SYNC=1 "$DOTFILES/scripts/sync/sync-dotfiles.sh"

echo ""
echo "Applying macOS defaults..."
"$DOTFILES/scripts/setup/apply-macos-defaults.py"

launch_domain="gui/$(id -u)"
for plist in "$HOME/Library/LaunchAgents"/com.tylerlaprade.*.plist; do
  [[ -e "$plist" ]] || continue
  launchctl bootstrap "$launch_domain" "$plist" 2>/dev/null || true
done

echo ""
echo "=== Next steps ==="
echo "  1. Sourcery auth:    sourcery login"
echo "  2. Kanata:           Grant accessibility permissions in System Preferences"
echo "  3. Karabiner:        Grant input monitoring permissions in System Preferences"
echo ""
if [[ $failed -ne 0 ]]; then
  echo "Some install steps failed."
  exit 1
fi
echo "Done! Restart your shell."
