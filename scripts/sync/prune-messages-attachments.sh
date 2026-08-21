#!/bin/bash
# Evict local iMessage attachment caches older than 30 days; iCloud keeps the
# originals. Mac Messages re-hoards attachments indefinitely and offers no
# offload setting. Group photos (files named GroupPhotoImage) are kept —
# pruning them blanks group icons until someone re-sets the photo. Safe to run
# with Messages open: the age filter excludes anything mid-transfer or in
# active use, and macOS keeps deleted-but-open files alive until closed.
# Triggered weekly by ~/Library/LaunchAgents/com.tylerlaprade.prune-messages-attachments.plist.
set -euo pipefail

echo "=== $(date) ==="

DIR="$HOME/Library/Messages/Attachments"
if [ -d "$DIR" ]; then
  before=$(/usr/bin/du -sm "$DIR" | cut -f1)
  find "$DIR" -type f ! -name 'GroupPhotoImage' -mtime +30 -delete
  find "$DIR" -mindepth 1 -type d -empty -delete
  after=$(/usr/bin/du -sm "$DIR" | cut -f1)
  echo "Attachments: pruned $((before - after)) MB (now ${after} MB)."
else
  echo "No attachments dir; skip."
fi

# Preview thumbnails regenerate from attachments.
CACHE="$HOME/Library/Messages/Caches"
if [ -d "$CACHE" ]; then
  before=$(/usr/bin/du -sm "$CACHE" | cut -f1)
  find "$CACHE" -type f -mtime +30 -delete
  find "$CACHE" -mindepth 1 -type d -empty -delete
  after=$(/usr/bin/du -sm "$CACHE" | cut -f1)
  echo "Caches: pruned $((before - after)) MB (now ${after} MB)."
fi
