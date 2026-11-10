#!/bin/bash
# Usage: lock-screen
# Locks the Mac's screen at once, the same lock as Control-Command-Q. Sleeping
# the display isn't a lock: macOS locks only after its password delay (60
# seconds here), so a key pressed sooner wakes the Mac unlocked. The lock call
# lives in a private framework, which ships only inside the system's shared
# library cache, so the path below exists for the loader but not on disk.
set -euo pipefail

/usr/bin/python3 -I -c 'import ctypes; ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login").SACLockScreenImmediate()'
