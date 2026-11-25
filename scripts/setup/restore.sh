#!/bin/bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
role=$("$DOTFILES/scripts/bin/machine-role.sh")

backup="${1:-}"
for default in "$HOME/machine-backup" "$HOME/Desktop/machine-backup.zip"; do
  [[ -z "$backup" && -e "$default" ]] && backup="$default"
done

echo "=== Restore from backup ==="

if [[ -d "$backup" ]]; then
  RESTORE_DIR="$(cd "$backup" && pwd)"
elif [[ -f "$backup" ]]; then
  echo "Decrypting archive..."
  archive_dir="$(cd "$(dirname "$backup")" && pwd)"
  unzip -o "$backup" -d "$archive_dir"
  RESTORE_DIR="$archive_dir/machine-backup"
else
  echo "Usage: restore.sh [path to the machine-backup folder or machine-backup.zip]"
  echo "Default: ~/machine-backup, where pre-wipe.sh copies over the network, then ~/Desktop/machine-backup.zip"
  exit 1
fi
echo "Restoring from $RESTORE_DIR"

put_back() {
  local name="$1" destination="$2"
  echo "  ~/${destination#"$HOME"/}/"
  if [[ -d "$RESTORE_DIR/$name" ]]; then
    mkdir -p "$destination"
    cp -a "$RESTORE_DIR/$name/." "$destination/"
  else
    echo "    (not in the backup, skipping)"
  fi
}

put_back_file() {
  local name="$1" destination="$2"
  echo "  ~/${destination#"$HOME"/}"
  if [[ ! -f "$RESTORE_DIR/$name" ]]; then
    echo "    (not in the backup, skipping)"
    return
  fi
  cmp -s "$RESTORE_DIR/$name" "$destination" && return
  if [[ -e "$destination" ]]; then
    local kept
    kept="$destination.pre-restore-$(date +%Y%m%d%H%M%S)"
    mv "$destination" "$kept"
    echo "    Kept the one already here as ${kept##*/}"
  fi
  mkdir -p "$(dirname "$destination")"
  cp -a "$RESTORE_DIR/$name" "$destination"
}

echo ""
echo "--- Restoring Fondly ---"
put_back Keys "$HOME/Keys"
put_back "Provisioning Profiles" "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"
put_back Fondly "$HOME/Pictures/Fondly"

# Secrets
echo ""
echo "--- Restoring secrets ---"

echo "  ~/.ssh/"
mkdir -p "$HOME/.ssh"
find "$HOME/.ssh" -maxdepth 1 -type l -delete 2>/dev/null || true
chmod -R u+w "$HOME/.ssh" 2>/dev/null || true
cp -a "$RESTORE_DIR/ssh/"* "$HOME/.ssh/"
chmod 700 "$HOME/.ssh"
chmod 600 "$HOME/.ssh/"id_* 2>/dev/null || true

echo "  ~/.gnupg/"
mkdir -p "$HOME/.gnupg"
find "$HOME/.gnupg" -maxdepth 1 -type l -delete 2>/dev/null || true
chmod -R u+w "$HOME/.gnupg" 2>/dev/null || true
cp -a "$RESTORE_DIR/gnupg/"* "$HOME/.gnupg/"
chmod 700 "$HOME/.gnupg"

put_back aws "$HOME/.aws"

echo "  ~/.config/AWSVPNClient/"
if [[ -d "$RESTORE_DIR/AWSVPNClient" ]]; then
  mkdir -p "$HOME/.config/AWSVPNClient"
  cp -a "$RESTORE_DIR/AWSVPNClient/"* "$HOME/.config/AWSVPNClient/"
fi

put_back_file zshrc.local "$HOME/.zshrc.local"
put_back_file gh/hosts.yml "$HOME/.config/gh/hosts.yml"
put_back_file graphite/user_config "$HOME/.config/graphite/user_config"

echo "  Atlassian CLI config"
if [[ -d "$RESTORE_DIR/acli" ]]; then
  mkdir -p "$HOME/.config/acli"
  cp -a "$RESTORE_DIR/acli/"* "$HOME/.config/acli/"
fi

put_back_file sourcery/auth.yaml "$HOME/.config/sourcery/auth.yaml"
put_back_file netrc "$HOME/.netrc"
[[ -f "$RESTORE_DIR/netrc" ]] && chmod 600 "$HOME/.netrc"
put_back_file zshenv.local "$HOME/.zshenv.local"

old_home=""
if [[ -f "$RESTORE_DIR/claude-memories/.home" ]]; then
  old_home=$(<"$RESTORE_DIR/claude-memories/.home")
fi

# Claude memories
# Project keys are derived from repo paths. If the home directory changed,
# remap old keys to match the new home directory.
echo ""
echo "--- Restoring Claude memories ---"
new_home_slug=$(echo "$HOME" | tr '/' '-')
old_home_slug=$(echo "$old_home" | tr '/' '-')
if [[ -n "$old_home_slug" && "$old_home_slug" != "$new_home_slug" ]]; then
  echo "  Remapping project keys: $old_home_slug -> $new_home_slug"
fi
for projectdir in "$RESTORE_DIR/claude-memories"/*/; do
  [[ -d "$projectdir" ]] || continue
  project=$(basename "$projectdir")

  if [[ -n "$old_home_slug" && "$old_home_slug" != "$new_home_slug" ]]; then
    project="${project/$old_home_slug/$new_home_slug}"
  fi

  dest="$HOME/.claude/projects/$project/memory"
  mkdir -p "$dest"
  cp "$projectdir"*.md "$dest/"
  echo "  $project"
done

# Codex's settings name folders by their full path, so a new home directory
# replaces the old one in them.
echo ""
echo "--- Restoring agent settings ---"
old_folder="$old_home/"
new_folder="$HOME/"
for settings in .codex/config.toml .codex/keybindings.json .codex/rules/default.rules .grok/pager.toml; do
  carried="$RESTORE_DIR/agent-settings/$settings"
  if [[ -n "$old_home" && "$old_home" != "$HOME" && -f "$carried" ]] && grep -qF "$old_folder" "$carried"; then
    contents=$(<"$carried")
    printf '%s\n' "${contents//"$old_folder"/$new_folder}" >"$carried"
  fi
  put_back_file "agent-settings/$settings" "$HOME/$settings"
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
fonts=("$RESTORE_DIR/Fonts/"*)
echo "  ${#fonts[@]} fonts restored"

# Code signing
if [[ -f "$RESTORE_DIR/xcode.developerprofile" ]]; then
  echo ""
  echo "--- Code signing ---"
  echo "  Import the Apple ID and signing identities into Xcode: double-click"
  echo "  $RESTORE_DIR/xcode.developerprofile"
fi

# Cleanup
echo ""
read -p "Remove $RESTORE_DIR, signing file included? [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] && { chmod -RN "$RESTORE_DIR" 2>/dev/null; rm -rf "$RESTORE_DIR"; }

echo ""
echo "=== Restore complete ==="
echo ""
steps=(
  "Run: $DOTFILES/scripts/setup/apply-macos-defaults.py"
  "/etc/hosts entries are applied by install.sh from scripts/setup/hosts"
)
case "$role" in
  agent)
    steps+=("Fondly: install Xcode as its DISTRIBUTION.md says, then run Distribution/check-testflight-signing.sh there")
    ;;
  personal)
    steps+=(
      "Verify Brave Sync pulled everything down"
      "Grant Input Monitoring and Accessibility for $HOME/.local/bin/kanata (do NOT install Karabiner-Elements: kanata-setup installs only the Karabiner driver release that Kanata supports)"
      "Re-add login items if needed: Discord, Google Calendar (do NOT add Monologue: it opens itself at login, so a login item here makes it open twice)"
      "Reinstall Google Calendar PWA in Brave (three dots > Install page as app): https://calendar.google.com/calendar/r"
    )
    ;;
  *)
    echo "restore.sh has no steps for the role $role." >&2
    exit 1
    ;;
esac
echo "Remaining manual steps:"
printf '  - %s\n' "${steps[@]}"
