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
mkdir -p "$HOME/.ssh"
cp -a "$RESTORE_DIR/ssh/"* "$HOME/.ssh/"
chmod 700 "$HOME/.ssh"
chmod 600 "$HOME/.ssh/"id_* 2>/dev/null || true

echo "  ~/.gnupg/"
mkdir -p "$HOME/.gnupg"
cp -a "$RESTORE_DIR/gnupg/"* "$HOME/.gnupg/"
chmod 700 "$HOME/.gnupg"

echo "  ~/.aws/"
mkdir -p "$HOME/.aws"
cp -a "$RESTORE_DIR/aws/"* "$HOME/.aws/"

echo "  ~/.config/AWSVPNClient/"
if [[ -d "$RESTORE_DIR/AWSVPNClient" ]]; then
  mkdir -p "$HOME/.config/AWSVPNClient"
  cp -a "$RESTORE_DIR/AWSVPNClient/"* "$HOME/.config/AWSVPNClient/"
fi

echo "  ~/.zshrc.local"
cp "$RESTORE_DIR/zshrc.local" "$HOME/.zshrc.local"

echo "  Graphite user_config"
if [[ -f "$RESTORE_DIR/graphite/user_config" ]]; then
  mkdir -p "$HOME/.config/graphite"
  cp "$RESTORE_DIR/graphite/user_config" "$HOME/.config/graphite/user_config"
fi

echo "  Atlassian CLI config"
if [[ -d "$RESTORE_DIR/acli" ]]; then
  mkdir -p "$HOME/.config/acli"
  cp -a "$RESTORE_DIR/acli/"* "$HOME/.config/acli/"
fi

echo "  Sourcery auth"
if [[ -f "$RESTORE_DIR/sourcery/auth.yaml" ]]; then
  mkdir -p "$HOME/.config/sourcery"
  cp "$RESTORE_DIR/sourcery/auth.yaml" "$HOME/.config/sourcery/auth.yaml"
fi

# Claude memories
# Project keys are derived from repo paths. If the home directory changed,
# remap old keys to match the new home directory.
echo ""
echo "--- Restoring Claude memories ---"
old_home_slug=""
new_home_slug=$(echo "$HOME" | tr '/' '-')
for projectdir in "$RESTORE_DIR/claude-memories"/*/; do
  [[ -d "$projectdir" ]] || continue
  project=$(basename "$projectdir")

  # Detect old home slug from the first project key
  if [[ -z "$old_home_slug" ]]; then
    # Project keys look like -Users-tyler-Code-condor
    # Try to find the common prefix that differs from current $HOME
    old_home_slug=$(echo "$project" | sed -E 's/(-Code-.*)//' )
    if [[ "$old_home_slug" != "$new_home_slug" ]]; then
      echo "  Remapping project keys: $old_home_slug -> $new_home_slug"
    fi
  fi

  # Remap the project key if home directory changed
  if [[ "$old_home_slug" != "$new_home_slug" ]]; then
    project="${project/$old_home_slug/$new_home_slug}"
  fi

  dest="$HOME/.claude/projects/$project/memory"
  mkdir -p "$dest"
  cp "$projectdir"*.md "$dest/"
  echo "  $project"
done

# Personal files
echo ""
echo "--- Restoring personal files ---"

echo "  ~/Documents/"
cp -R "$RESTORE_DIR/Documents/"* "$HOME/Documents/" 2>/dev/null || true

echo "  ~/Desktop/"
for item in "$RESTORE_DIR/Desktop/"*; do
  [[ -e "$item" ]] && cp -R "$item" "$HOME/Desktop/"
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
mkdir -p "$HOME/Library/Fonts"
cp -R "$RESTORE_DIR/Fonts/"* "$HOME/Library/Fonts/"
echo "  $(ls "$RESTORE_DIR/Fonts/" | wc -l | tr -d ' ') fonts restored"

# Cleanup
echo ""
read -p "Remove backup directory? (archive kept) [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] && { chmod -RN "$RESTORE_DIR" 2>/dev/null; rm -rf "$RESTORE_DIR"; }

echo ""
echo "=== Restore complete ==="
echo ""
echo "Remaining manual steps:"
echo "  1. Verify Brave Sync pulled everything down"
echo "  2. Run: $HOME/Code/dotfiles/scripts/apply-macos-defaults.py"
echo "  3. Grant accessibility permissions for Kanata"
echo "  4. Grant input monitoring permissions for Karabiner"
echo "  5. Add custom /etc/hosts entries:"
echo "     sudo sh -c 'echo \"127.0.0.1       local.paqarina.dev\" >> /etc/hosts'"
echo "  6. Re-add login items: Hyperkey, Discord, Granola, Graphite, Google Calendar, Slack"
echo "  7. Reinstall Google Calendar PWA in Brave (three dots > Install page as app):"
echo "     https://calendar.google.com/calendar/r"
