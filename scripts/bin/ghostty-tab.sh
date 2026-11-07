#!/bin/bash
# Usage: ghostty-tab <folder> <command line> [automation owner executable]
# Types <command line> into a new shell tab of the main Ghostty window, the one
# with the most tabs, and prints the tab's id. Ghostty's scripting selects every
# tab it adds, makes its window key and activates the app, with no option to
# skip any of that, so this restores the tab, window and app Tyler was in.
set -euo pipefail

folder=${1:?folder}
command_line=${2:?command line}

# A tab opened while Screen Time has Ghostty locked out crashes Ghostty when the lockout lifts.
"$HOME/.cargo/bin/session-guard" lockout --wait

if [[ $# -eq 3 ]]; then
  "$3" check
fi

# A system alert can hold the front as a UIElement app. Asking to activate one stalled this script for 119 seconds.
front_app=$(lsappinfo front)
front_app_type=$(lsappinfo info -only ApplicationType "$front_app")
front_app_id=""
if [[ $front_app_type == *'"Foreground"'* ]]; then
  front_app_id=$(lsappinfo info -only bundleID "$front_app" | sed -n 's/.*="\(.*\)"$/\1/p')
fi

osascript - "$folder" "$command_line" "$PATH" "$front_app_id" <<'APPLESCRIPT'
on run {workDir, commandLine, fullPath, frontAppId}
  tell application "Ghostty"
    set cfg to new surface configuration
    set initial input of cfg to commandLine & linefeed
    set initial working directory of cfg to workDir
    set environment variables of cfg to {"PATH=" & fullPath}
    if (count of windows) is 0 then
      set w to new window with configuration cfg
      set newTabId to id of selected tab of w
      if frontAppId is not "" then tell application id frontAppId to activate
      return newTabId
    end if
    set mainWindow to front window
    repeat with candidateWindow in windows
      if (count of tabs of candidateWindow) > (count of tabs of mainWindow) then set mainWindow to candidateWindow
    end repeat
    -- A live "selected tab" reference follows the selection to the new tab, so keep the id.
    set previousId to id of selected tab of mainWindow
    set previousWindowId to id of front window
    set t to new tab in mainWindow with configuration cfg
    if selected of t then select tab (first tab of mainWindow whose id is previousId)
    if id of front window is not previousWindowId then activate window (first window whose id is previousWindowId)
    set newTabId to id of t
  end tell
  if frontAppId is not "" then tell application id frontAppId to activate
  return newTabId
end run
APPLESCRIPT
