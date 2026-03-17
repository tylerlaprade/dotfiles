#!/bin/bash
set -e

BACKUP_DIR="$HOME/Desktop/machine-backup"
ARCHIVE="$HOME/Desktop/machine-backup.zip"

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Pre-Wipe Backup ==="

# Sync dotfiles and push
echo ""
echo "--- Syncing dotfiles ---"
cd "$DOTFILES"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "  ✗ Dotfiles repo has uncommitted changes. Commit or stash first."
  exit 1
fi
"$DOTFILES/scripts/sync-dotfiles.sh"
git add -A
git diff --cached --quiet || git commit -m "Pre-wipe sync"
git push

# Check for uncommitted/unpushed work in ~/Code
echo ""
echo "--- Scanning ~/Code for uncommitted/unpushed work ---"
dirty=0
for dir in "$HOME/Code/"*/; do
  (cd "$dir" 2>/dev/null && if git rev-parse --is-inside-work-tree &>/dev/null; then
    st=$(git status --porcelain 2>/dev/null)
    up=$(git log --oneline @{u}..HEAD 2>/dev/null)
    if [[ -n "$st" || -n "$up" ]]; then
      echo "  ⚠ $(basename "$dir")"
      [[ -n "$st" ]] && echo "    Uncommitted: $(echo "$st" | wc -l | tr -d ' ') files"
      [[ -n "$up" ]] && echo "    Unpushed: $(echo "$up" | wc -l | tr -d ' ') commits"
      dirty=1
    fi
  fi) || true
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
  read -p "  Continue without fixing? [y/N] " -r
  [[ "$REPLY" =~ ^[Yy]$ ]] || exit 1
fi
echo ""
echo "  Write down your Brave sync code (Brave > Settings > Sync)."
echo "  You'll need it after Brave is installed on the new machine."
read -p "  Done? [y/N] " -r
[[ "$REPLY" =~ ^[Yy]$ ]] || exit 1

# Build backup directory
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

echo ""
echo "--- Copying files ---"

# Secrets
echo "  ~/.ssh/"
cp -a "$HOME/.ssh" "$BACKUP_DIR/ssh"

echo "  ~/.gnupg/"
cp -a "$HOME/.gnupg" "$BACKUP_DIR/gnupg"

echo "  ~/.aws/"
cp -a "$HOME/.aws" "$BACKUP_DIR/aws"

echo "  ~/.config/AWSVPNClient/"
cp -a "$HOME/.config/AWSVPNClient" "$BACKUP_DIR/AWSVPNClient" 2>/dev/null || echo "    (not found, skipping)"

echo "  ~/.zshrc.local"
cp "$HOME/.zshrc.local" "$BACKUP_DIR/zshrc.local"

echo "  ~/.config/graphite/user_config"
mkdir -p "$BACKUP_DIR/graphite"
cp "$HOME/.config/graphite/user_config" "$BACKUP_DIR/graphite/user_config" 2>/dev/null || echo "    (not found, skipping)"

echo "  ~/.config/acli/"
cp -a "$HOME/.config/acli" "$BACKUP_DIR/acli" 2>/dev/null || echo "    (not found, skipping)"

echo "  ~/.config/sourcery/"
mkdir -p "$BACKUP_DIR/sourcery"
cp "$HOME/.config/sourcery/auth.yaml" "$BACKUP_DIR/sourcery/auth.yaml" 2>/dev/null || echo "    (not found, skipping)"

# Claude memories
echo "  Claude memory files"
mkdir -p "$BACKUP_DIR/claude-memories"
for memdir in "$HOME/.claude/projects/"*/memory; do
  [[ -d "$memdir" ]] || continue
  project=$(basename "$(dirname "$memdir")")
  # Only copy if there are actual files
  if ls "$memdir"/*.md &>/dev/null; then
    mkdir -p "$BACKUP_DIR/claude-memories/$project"
    cp "$memdir"/*.md "$BACKUP_DIR/claude-memories/$project/"
  fi
done

# Personal files
echo "  ~/Documents/ (excluding Zoom, screen recordings)"
mkdir -p "$BACKUP_DIR/Documents"
for item in "$HOME/Documents/"*; do
  name=$(basename "$item")
  [[ "$name" == "Zoom" ]] && continue
  [[ "$name" == Screen\ Recording* ]] && continue
  cp -a "$item" "$BACKUP_DIR/Documents/$name"
done

echo "  ~/Desktop/ (excluding this backup)"
mkdir -p "$BACKUP_DIR/Desktop"
for item in "$HOME/Desktop/"*; do
  name=$(basename "$item")
  [[ "$name" == "machine-backup" || "$name" == "machine-backup.zip" ]] && continue
  cp -a "$item" "$BACKUP_DIR/Desktop/$name"
done

# History files
echo "  Shell/REPL histories"
mkdir -p "$BACKUP_DIR/histories"
for f in .zsh_history .psql_history .python_history .node_repl_history; do
  [[ -f "$HOME/$f" ]] && cp "$HOME/$f" "$BACKUP_DIR/histories/$f"
done

# Fonts
echo "  ~/Library/Fonts/"
cp -a "$HOME/Library/Fonts" "$BACKUP_DIR/Fonts"

# Create encrypted zip
echo ""
echo "--- Creating encrypted archive ---"
rm -f "$ARCHIVE"
cd "$(dirname "$BACKUP_DIR")"
zip -r -e "$ARCHIVE" "$(basename "$BACKUP_DIR")"
xattr -rc "$BACKUP_DIR" 2>/dev/null; chmod -R u+rwx "$BACKUP_DIR" 2>/dev/null; rm -rf "$BACKUP_DIR"

echo ""
echo "=== Backup complete ==="
echo "Archive: $ARCHIVE"
echo ""
echo "Next steps:"
echo "  1. AirDrop $ARCHIVE to the new machine"
echo "  2. Push all repos with unpushed work"
echo "  3. On the new machine, run bootstrap.sh then restore.sh"
echo "  4. Rotate your GitHub PAT after migration"
