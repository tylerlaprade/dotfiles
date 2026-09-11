#!/bin/bash
# Restart AltTab once a day so WindowServer gives back the memory AltTab pins.
#
# Why: AltTab keeps a live preview of every window, and WindowServer holds the
# backing surface for each preview. Those surfaces are never released while
# AltTab runs, so WindowServer only grows. Measured on 2026-09-11 after 28 days
# of uptime: WindowServer sat at 3.5 GB resident, and quitting AltTab returned
# 1 GB within 20 seconds. Upstream tracks the growth as lwouis/alt-tab-macos
# issues #4255 and #4535, both open with no fix; restarting AltTab is the only
# known workaround. Only a logout resets WindowServer itself, so this job keeps
# the leak small between logouts instead of curing it.
#
# Triggered daily at 04:30 by ~/Library/LaunchAgents/com.tylerlaprade.refresh-alttab.plist.
# If the Mac is asleep at 04:30, launchd runs the missed job at the next wake.
set -euo pipefail

export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

echo "=== $(date) ==="

if ! pgrep -xq AltTab; then
  echo "AltTab is not running; nothing to refresh"
  exit 0
fi

# SIGTERM instead of an AppleScript `quit`: sending Apple events from a launchd
# job needs an Automation permission that macOS cannot prompt for in the
# background, so the quit would fail silently. AltTab stores its preferences
# through NSUserDefaults as they change, so a plain terminate loses nothing.
killall AltTab

# Wait for the old process to be gone. `open` on a process that is still
# exiting only focuses the dying instance and the relaunch is skipped.
for _ in $(seq 1 20); do
  pgrep -xq AltTab || break
  sleep 0.5
done
if pgrep -xq AltTab; then
  echo "AltTab did not exit within 10 seconds; leaving it alone"
  exit 1
fi

# -g launches in the background so a relaunch never steals focus from
# whatever is on screen when the job fires.
open -g -a AltTab
echo "AltTab restarted"
