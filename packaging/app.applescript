-- Sensor Calibration Studio — macOS app wrapper (compiled by
-- packaging/build_mac_app.sh into a stay-open applet).
--
-- The applet stays running while the local server runs, so macOS treats it
-- as a normal app: Dock icon, ⌘Q stops the server, clicking the Dock icon
-- (or opening the app again) reopens the browser, and the app quits itself
-- when the server stops (Quit button in the page, or idle timeout).

property serverURL : ""

on run
	startServer()
end run

on reopen
	startServer()
end reopen

on startServer()
	set script_ to POSIX path of (path to resource "start-server.sh")
	try
		set serverURL to do shell script quoted form of script_
	on error errMsg
		display alert "Sensor Calibration Studio" message ("The server failed to start: " & errMsg & return & return & "Details are in ~/Library/Logs/Sensor Calibration Studio.log") as critical
		quit
	end try
end startServer

on idle
	if serverURL is not "" then
		try
			do shell script "/usr/bin/curl -fs --max-time 3 " & quoted form of (serverURL & "/api/app/info")
		on error
			-- The server stopped (Quit button or idle timeout): close the app too.
			set serverURL to ""
			quit
		end try
	end if
	return 10
end idle

on quit
	if serverURL is not "" then
		try
			do shell script "/usr/bin/curl -fs -X POST --max-time 3 " & quoted form of (serverURL & "/api/app/quit")
		end try
	end if
	continue quit
end quit
