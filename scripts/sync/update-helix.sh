#!/bin/bash
# Install the latest gj1118/helix GitHub release (binary plus runtime) into
# ~/.local/share/helix and link hx into ~/.local/bin. Triggered weekly by
# ~/Library/LaunchAgents/com.tylerlaprade.update-helix.plist.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="gj1118/helix"
SHARE="$HOME/.local/share/helix"
BIN="$HOME/.local/bin"
ZSH_COMPLETIONS="$HOME/.local/share/zsh/site-functions"

machine=$(uname -m)
case "$machine" in
  arm64) ARCH="aarch64" ;;
  x86_64) ARCH="x86_64" ;;
  *) echo "Unsupported architecture: $machine"; exit 1 ;;
esac

run_started=$(date)
echo "=== $run_started ==="
tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p')
[[ -n "$tag" ]] || { echo "No release tag found for $REPO"; exit 1; }

name="helix-$tag-$ARCH-macos"
current_name=$(readlink "$SHARE/current") || true
if [[ -d "$SHARE/$name" && "$current_name" == "$name" ]]; then
  echo "hx already at $tag"
  exit 0
fi

echo "Installing $name"
mkdir -p "$SHARE" "$BIN" "$ZSH_COMPLETIONS"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://github.com/$REPO/releases/download/$tag/$name.tar.xz" -o "$tmp/$name.tar.xz"
tar xJf "$tmp/$name.tar.xz" -C "$SHARE"
ln -sfn "$name" "$SHARE/current"
ln -sfn "$SHARE/current/hx" "$BIN/hx"
ln -sfn "$SHARE/current/contrib/completion/hx.zsh" "$ZSH_COMPLETIONS/_hx"
for old in "$SHARE"/helix-*-macos; do
  [[ "$old" == "$SHARE/$name" ]] || rm -rf "$old"
done
hx_version=$("$BIN/hx" --version)
echo "Installed: $hx_version"
