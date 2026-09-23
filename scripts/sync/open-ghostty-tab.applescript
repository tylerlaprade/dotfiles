-- Runs a command in a new, unselected tab of the front Ghostty window, then
-- hands focus back to whatever app was in front. Ghostty holds Full Disk
-- Access, so the command can read folders launchd jobs cannot.
on run {commandPath}
	tell application "System Events"
		set previousApp to name of first application process whose frontmost is true
	end tell

	tell application "Ghostty"
		set cfg to new surface configuration
		set command of cfg to commandPath
		set wait after command of cfg to true
		if (count of windows) is 0 then
			new window with configuration cfg
		else
			set w to front window
			set previousId to id of selected tab of w
			set t to new tab in w with configuration cfg
			if selected of t then select tab (first tab of w whose id is previousId)
		end if
	end tell

	if previousApp is not "ghostty" then
		tell application "System Events" to set frontmost of process previousApp to true
	end if
end run
