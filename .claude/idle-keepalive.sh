#!/bin/bash
# Keep Claude Code sessions alive past the 1hr idle threshold.
# Per April 23 postmortem, sessions idle >1hr get thinking history cleared
# server-side with no recovery path. This pings any session in the 55-60min
# idle window via headless `claude --resume <id> -p "."`, which counts as a
# real API turn and resets the server-side idle clock.
#
# Run via launchd every 5min. The 5min cron tick × 5min ping window means
# every idle session gets pinged exactly once before the cliff.

set -u

CLAUDE_BIN=/Users/tyler/.local/bin/claude
LOG=/tmp/claude-keepalive.log
LOWER=${CLAUDE_KEEPALIVE_LOWER:-3300}
UPPER=${CLAUDE_KEEPALIVE_UPPER:-3600}

now=$(date +%s)

find ~/.claude/projects -maxdepth 2 -name "*.jsonl" -type f 2>/dev/null | while read -r f; do
  mtime=$(stat -f %m "$f" 2>/dev/null) || continue
  age=$(( now - mtime ))
  [ "$age" -lt "$LOWER" ] && continue
  [ "$age" -ge "$UPPER" ] && continue

  sid=$(basename "$f" .jsonl)
  printf '%s ping sid=%s age=%ds\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$sid" "$age" >> "$LOG"
  (
    "$CLAUDE_BIN" --resume "$sid" \
      --tools "" \
      --append-system-prompt "AUTOMATED KEEPALIVE PING. Reply with exactly '.' (one period). Do NOT execute any tools. Do NOT analyze prior context. This is a no-op to keep the session alive." \
      -p "." </dev/null >/dev/null 2>&1
    rc=$?
    printf '%s done sid=%s rc=%d\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$sid" "$rc" >> "$LOG"
  ) &
done

wait
