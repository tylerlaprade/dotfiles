#!/bin/bash
# Install the latest gj1118/helix GitHub release (binary plus runtime) into
# ~/.local/share/helix and link hx into ~/.local/bin. Triggered weekly by
# ~/Library/LaunchAgents/com.tylerlaprade.update-helix.plist.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="gj1118/helix"
SHARE="$HOME/.local/share/helix"
BIN="$HOME/.local/bin"

case "$(uname -m)" in
  arm64) ARCH="aarch64" ;;
  x86_64) ARCH="x86_64" ;;
  *) echo "Unsupported architecture: $(uname -m)"; exit 1 ;;
esac

echo "=== $(date) ==="
tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p')
[[ -n "$tag" ]] || { echo "No release tag found for $REPO"; exit 1; }

name="helix-$tag-$ARCH-macos"
if [[ -d "$SHARE/$name" && "$(readlink "$SHARE/current")" == "$name" ]]; then
  echo "hx already at $tag"
  exit 0
fi

echo "Installing $name"
mkdir -p "$SHARE" "$BIN"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://github.com/$REPO/releases/download/$tag/$name.tar.xz" -o "$tmp/$name.tar.xz"
tar xJf "$tmp/$name.tar.xz" -C "$SHARE"
ln -sfn "$name" "$SHARE/current"
ln -sfn "$SHARE/current/hx" "$BIN/hx"
for old in "$SHARE"/helix-*-macos; do
  [[ "$old" == "$SHARE/$name" ]] || rm -rf "$old"
done
echo "Installed: $("$BIN/hx" --version)"
