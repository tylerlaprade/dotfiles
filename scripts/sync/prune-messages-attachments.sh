#!/bin/bash
# Evict local iMessage attachment caches older than 30 days; iCloud keeps the
# originals. Mac Messages re-hoards attachments indefinitely and offers no
# offload setting. Group photos (files named GroupPhotoImage) are kept —
# pruning them blanks group icons until someone re-sets the photo. Safe to run
# with Messages open: the age filter excludes anything mid-transfer or in
# active use, and macOS keeps deleted-but-open files alive until closed.
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.prune-messages-attachments.plist.
set -euo pipefail

DIR="$HOME/Library/Messages/Attachments"

echo "=== $(date) ==="
[ -d "$DIR" ] || { echo "No attachments dir; nothing to do."; exit 0; }

before=$(/usr/bin/du -sm "$DIR" | cut -f1)
find "$DIR" -type f ! -name 'GroupPhotoImage' -mtime +30 -delete
find "$DIR" -mindepth 1 -type d -empty -delete
after=$(/usr/bin/du -sm "$DIR" | cut -f1)
echo "Pruned $((before - after)) MB (now ${after} MB)."
