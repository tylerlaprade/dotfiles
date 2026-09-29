on run {commandPath, logPath}
	tell application "System Events"
		set previousApp to name of first application process whose frontmost is true
	end tell

	tell application "Ghostty"
		set cfg to new surface configuration
		set initial working directory of cfg to POSIX path of (path to home folder)
		set initial input of cfg to (quoted form of commandPath) & " >> " & (quoted form of logPath) & " 2>&1; exit" & linefeed
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
