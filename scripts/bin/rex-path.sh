#!/bin/bash
# Usage: rex-path
# Prints the `rex` command, or fails when Rex is not installed. It is on PATH
# inside Rex's own terminals and otherwise only inside the app, which
# LaunchServices finds under whatever name it has, without launching it.
set -euo pipefail

command -v rex && exit 0

app=$(osascript -e 'use framework "AppKit"' -e 'on run {bundleId}
  set appUrl to current application'\''s NSWorkspace'\''s sharedWorkspace()'\''s URLForApplicationWithBundleIdentifier:bundleId
  if appUrl is missing value then return ""
  return (appUrl'\''s |path|()) as text
end run' com.superlogical.rex)
for candidate in "${app}"/Contents/Helpers/*/Contents/MacOS/rex; do
  [[ -x "${candidate}" ]] && { printf '%s\n' "${candidate}"; exit 0; }
done
exit 1
