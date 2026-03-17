#!/bin/bash
set -e

ARCHIVE="${1:-$HOME/Desktop/machine-backup.zip}"
RESTORE_DIR="$HOME/Desktop/machine-backup"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Usage: restore.sh [path-to-machine-backup.zip]"
  echo "Default: ~/Desktop/machine-backup.zip"
  exit 1
fi

echo "=== Restore from backup ==="

# Unzip
echo "Decrypting archive..."
cd "$(dirname "$ARCHIVE")"
unzip -o "$ARCHIVE"

# Secrets
echo ""
echo "--- Restoring secrets ---"

echo "  ~/.ssh/"
mkdir -p ~/.ssh
cp -a "$RESTORE_DIR/ssh/"* ~/.ssh/
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_* 2>/dev/null || true

echo "  ~/.gnupg/"
mkdir -p ~/.gnupg
cp -a "$RESTORE_DIR/gnupg/"* ~/.gnupg/
chmod 700 ~/.gnupg

echo "  ~/.aws/"
mkdir -p ~/.aws
cp -a "$RESTORE_DIR/aws/"* ~/.aws/

echo "  ~/.zshrc.local"
cp "$RESTORE_DIR/zshrc.local" ~/.zshrc.local

echo "  Graphite user_config"
if [[ -f "$RESTORE_DIR/graphite/user_config" ]]; then
  mkdir -p ~/.config/graphite
  cp "$RESTORE_DIR/graphite/user_config" ~/.config/graphite/user_config
fi

# Claude memories
echo ""
echo "--- Restoring Claude memories ---"
for projectdir in "$RESTORE_DIR/claude-memories"/*/; do
  [[ -d "$projectdir" ]] || continue
  project=$(basename "$projectdir")
  dest="$HOME/.claude/projects/$project/memory"
  mkdir -p "$dest"
  cp "$projectdir"*.md "$dest/"
  echo "  $project"
done

# Personal files
echo ""
echo "--- Restoring personal files ---"

echo "  ~/Documents/"
cp -a "$RESTORE_DIR/Documents/"* ~/Documents/ 2>/dev/null || true

echo "  ~/Desktop/"
for item in "$RESTORE_DIR/Desktop/"*; do
  [[ -e "$item" ]] && cp -a "$item" ~/Desktop/
done

# History files
echo ""
echo "--- Restoring histories ---"
for f in "$RESTORE_DIR/histories"/.*; do
  name=$(basename "$f")
  [[ "$name" == "." || "$name" == ".." ]] && continue
  cp "$f" "$HOME/$name"
  echo "  $name"
done

# Fonts
echo ""
echo "--- Restoring fonts ---"
mkdir -p ~/Library/Fonts
cp -a "$RESTORE_DIR/Fonts/"* ~/Library/Fonts/
echo "  $(ls "$RESTORE_DIR/Fonts/" | wc -l | tr -d ' ') fonts restored"

# Cleanup
echo ""
read -p "Remove backup directory? (archive kept) [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] && rm -rf "$RESTORE_DIR"

echo ""
echo "=== Restore complete ==="
echo ""
echo "Remaining manual steps:"
echo "  1. Rotate your GitHub PAT (the one in ~/.zshrc.local)"
echo "  2. Verify Brave Sync pulled everything down"
echo "  3. Run: $HOME/Code/dotfiles/scripts/apply-macos-defaults.py"
echo "  4. Grant accessibility permissions for Kanata"
echo "  5. Grant input monitoring permissions for Karabiner"
