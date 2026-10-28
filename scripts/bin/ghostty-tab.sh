#!/bin/bash
# Usage: ghostty-tab <folder> <command line>
# Types <command line> into a new shell tab of the main Ghostty window, the one
# with the most tabs, and prints the tab's id. The tab Tyler is in stays
# selected, because Ghostty's scripting selects every tab it adds.
set -euo pipefail

folder=${1:?folder}
command_line=${2:?command line}

# A tab opened while Screen Time has Ghostty locked out crashes Ghostty when the lockout lifts.
"$HOME/.cargo/bin/session-guard" lockout --wait

osascript - "$folder" "$command_line" "$PATH" <<'APPLESCRIPT'
on run {workDir, commandLine, fullPath}
  tell application "Ghostty"
    set cfg to new surface configuration
    set initial input of cfg to commandLine & linefeed
    set initial working directory of cfg to workDir
    set environment variables of cfg to {"PATH=" & fullPath}
    if (count of windows) is 0 then
      set w to new window with configuration cfg
      return id of selected tab of w
    end if
    set mainWindow to front window
    repeat with candidateWindow in windows
      if (count of tabs of candidateWindow) > (count of tabs of mainWindow) then set mainWindow to candidateWindow
    end repeat
    -- A live "selected tab" reference follows the selection to the new tab, so keep the id.
    set previousId to id of selected tab of mainWindow
    set t to new tab in mainWindow with configuration cfg
    if selected of t then select tab (first tab of mainWindow whose id is previousId)
    return id of t
  end tell
end run
APPLESCRIPT
