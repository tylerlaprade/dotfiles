#!/bin/bash
# Usage: pre-wipe.sh [user@new-mac]
# Carries this Mac's files to another Mac: straight over Remote Login when given
# the new Mac, otherwise in an encrypted archive. restore.sh puts them back.
set -e

new_mac="${1:-}"
BACKUP_DIR="$HOME/Desktop/machine-backup"
ARCHIVE="$HOME/Desktop/machine-backup.zip"

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

RSYNC=/opt/homebrew/bin/rsync
rsync_options=(-aX --no-D --mkpath)
destination="$BACKUP_DIR"

carry() {
  local source="$1" name="$2"
  shift 2
  echo "  ~/${source#"$HOME"/}"
  if [[ -d "$source" ]]; then
    "$RSYNC" "${rsync_options[@]}" "$@" "$source/" "$destination/$name/"
  elif [[ -e "$source" ]]; then
    "$RSYNC" "${rsync_options[@]}" "$@" "$source" "$destination/$name"
  else
    echo "    (not found, skipping)"
  fi
}

echo "=== Pre-Wipe Backup ==="

if [[ -n "$new_mac" ]]; then
  echo ""
  echo "--- Connecting to $new_mac ---"
  ssh_sockets=$(mktemp -d /tmp/pre-wipe.XXXXXX)
  ssh_command=(ssh -o ControlMaster=auto -o "ControlPath=$ssh_sockets/%C" -o ControlPersist=yes)
  trap '"${ssh_command[@]}" -O exit "$new_mac" 2>/dev/null || true; rm -rf "$ssh_sockets"' EXIT
  if ! "${ssh_command[@]}" "$new_mac" "test -x $RSYNC && mkdir -p machine-backup && chmod 700 machine-backup"; then
    echo "  ✗ Cannot copy to $new_mac. On that Mac, turn on Remote Login in System Settings >"
    echo "    General > Sharing, and run bootstrap.sh, which installs $RSYNC."
    exit 1
  fi
  rsync_options+=(-e "${ssh_command[*]}" --rsync-path="$RSYNC")
  destination="$new_mac:machine-backup"
fi

# Sync dotfiles and push
echo ""
echo "--- Syncing dotfiles ---"
cd "$DOTFILES"
dotfiles_status=$(git status --porcelain)
if [[ -n "$dotfiles_status" ]]; then
  echo "  ⚠ Dotfiles repo has uncommitted changes, so it is not synced or pushed."
else
  "$DOTFILES/scripts/sync/sync-dotfiles.sh"
  git add -A
  git diff --cached --quiet || git commit -m "Pre-wipe sync"
  git push
fi

# Check for uncommitted/unpushed work in ~/Code
echo ""
echo "--- Scanning ~/Code for uncommitted/unpushed work ---"
dirty=0
for dir in "$HOME/Code/"*/; do
  git -C "$dir" rev-parse --is-inside-work-tree &>/dev/null || continue
  st=$(git -C "$dir" status --porcelain 2>/dev/null) || true
  up=$(git -C "$dir" log --oneline '@{u}..HEAD' 2>/dev/null) || true
  if [[ -n "$st" || -n "$up" ]]; then
    repo=$(basename "$dir")
    echo "  ⚠ $repo"
    if [[ -n "$st" ]]; then
      uncommitted_count=$(grep -c '' <<< "$st")
      echo "    Uncommitted: $uncommitted_count files"
    fi
    if [[ -n "$up" ]]; then
      unpushed_count=$(grep -c '' <<< "$up")
      echo "    Unpushed: $unpushed_count commits"
    fi
    dirty=1
  fi
done
if [[ "$dirty" -eq 1 ]]; then
  echo ""
  read -p "Repos above have uncommitted/unpushed work. Continue anyway? [y/N] " -r
  [[ "$REPLY" =~ ^[Yy]$ ]] || exit 1
else
  echo "  All clean."
fi

# Check Brave Sync
echo ""
echo "--- Brave Sync ---"
brave_prefs_dir="$HOME/Library/Application Support/BraveSoftware/Brave-Browser"
sync_ok=1
for profile_dir in "$brave_prefs_dir"/*/; do
  prefs="$profile_dir/Preferences"
  [[ -f "$prefs" ]] || continue
  name=$(python3 -c "import json; print(json.load(open('$prefs')).get('profile',{}).get('name','?'))" 2>/dev/null)
  sync_on=$(python3 -c "import json; s=json.load(open('$prefs')).get('sync',{}); print(s.get('has_setup_completed') or s.get('requested') or False)" 2>/dev/null)
  if [[ "$sync_on" != "True" ]]; then
    echo "  ⚠ Profile '$name': sync is OFF"
    sync_ok=0
  else
    echo "  ✓ Profile '$name': sync is on"
  fi
done
if [[ "$sync_ok" -eq 0 ]]; then
  echo ""
  echo "  Enable sync: Brave > Settings > Sync"
fi
echo ""
echo "  Write down your Brave sync code (Brave > Settings > Sync)."
echo "  You'll need it if the new machine gets Brave, or if you wipe this one."
read -p "  Ready to continue? [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] || exit 1

# Build backup directory
if [[ -d "$BACKUP_DIR" ]]; then
  chmod -RN "$BACKUP_DIR" 2>/dev/null; rm -rf "$BACKUP_DIR"
fi
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo ""
echo "--- Copying files ---"

# Fondly
carry "$HOME/Keys" Keys
carry "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles" "Provisioning Profiles"
carry "$HOME/Pictures/Fondly" Fondly

# Secrets
carry "$HOME/.ssh" ssh
# A lock file names the Mac that made it, so on another Mac GPG waits on it forever.
carry "$HOME/.gnupg" gnupg --exclude='*.lock' --exclude='.#lk*'
carry "$HOME/.aws" aws
carry "$HOME/.config/AWSVPNClient" AWSVPNClient
carry "$HOME/.zshrc.local" zshrc.local
carry "$HOME/.config/gh/hosts.yml" gh/hosts.yml
carry "$HOME/.config/graphite/user_config" graphite/user_config
carry "$HOME/.config/acli" acli
carry "$HOME/.config/sourcery/auth.yaml" sourcery/auth.yaml
carry "$HOME/.netrc" netrc
carry "$HOME/.zshenv.local" zshenv.local

echo ""
echo "  Code-signing identities live only in the keychain. In Xcode: Settings >"
echo "  Accounts > your Apple ID > Export Apple ID and Code Signing Assets..."
echo "  Save it as $BACKUP_DIR/xcode.developerprofile"
read -p "  Exported (or skipping)? [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] || exit 1

# Claude memories
mkdir -p "$BACKUP_DIR/claude-memories"
echo "$HOME" > "$BACKUP_DIR/claude-memories/.home"
for memdir in "$HOME/.claude/projects/"*/memory; do
  # Only copy if there are actual files
  if ls "$memdir"/*.md &>/dev/null; then
    project=$(basename "$(dirname "$memdir")")
    carry "$memdir" "claude-memories/$project" --include='*.md' --exclude='*'
  fi
done

# Personal files
carry "$HOME/Documents" Documents --exclude=/Zoom --exclude='/Screen Recording*'
carry "$HOME/Desktop" Desktop --exclude=/machine-backup --exclude=/machine-backup.zip

# History files
for f in .zsh_history .psql_history .python_history .node_repl_history; do
  carry "$HOME/$f" "histories/$f"
done

# Fonts
carry "$HOME/Library/Fonts" Fonts

if [[ -n "$new_mac" ]]; then
  "$RSYNC" "${rsync_options[@]}" "$BACKUP_DIR/" "$destination/"
  rm -rf "$BACKUP_DIR"
  echo ""
  echo "=== Copy complete ==="
  echo "Remaining steps:"
  echo "  1. Push all repos with unpushed work"
  echo "  2. On the new Mac, run restore.sh; it finds the copy in ~/machine-backup"
  exit 0
fi

# Create encrypted zip
echo ""
echo "--- Creating encrypted archive ---"
rm -f "$ARCHIVE"
cd "$(dirname "$BACKUP_DIR")"
zip -r -e "$ARCHIVE" "$(basename "$BACKUP_DIR")"
chmod -RN "$BACKUP_DIR" 2>/dev/null; rm -rf "$BACKUP_DIR"

echo ""
echo "=== Backup complete ==="
echo "Archive: $ARCHIVE"

# Serve the archive over HTTP for the new machine to download
local_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [[ -n "$local_ip" ]]; then
  echo ""
  echo "Serving archive at http://$local_ip:8000/$(basename "$ARCHIVE")"
  echo "On the new machine, run:"
  echo "  curl -o ~/Desktop/$(basename "$ARCHIVE") http://$local_ip:8000/$(basename "$ARCHIVE")"
  echo ""
  echo "Press Ctrl-C when transfer is complete."
  cd "$(dirname "$ARCHIVE")" && python3 -m http.server 8000
else
  echo ""
  echo "Could not determine local IP. Transfer manually:"
  open -R "$ARCHIVE"
fi

echo ""
echo "Remaining steps:"
echo "  1. Push all repos with unpushed work"
echo "  2. On the new machine, run bootstrap.sh then restore.sh"
