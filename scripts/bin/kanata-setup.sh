#!/bin/bash
# Run Homebrew's Kanata as a root daemon on the Karabiner driver release it
# supports, without Karabiner-Elements. Safe to run again; --dry-run checks and
# downloads everything but changes nothing.
set -euo pipefail

DOTFILES="${DOTFILES_DIR:-$HOME/Code/dotfiles}"
dry_run=0
[[ ${1:-} == --dry-run ]] && dry_run=1

kanata_src=/opt/homebrew/bin/kanata
kanata_dest="$HOME/.local/bin/kanata"
kanata_plist=/Library/LaunchDaemons/com.tylerlaprade.kanata.plist
kanata_service=system/com.tylerlaprade.kanata
kanata_log=/usr/local/var/log/kanata.out.log
driver_plist=/Library/LaunchDaemons/org.pqrs.Karabiner-VirtualHIDDevice-Daemon.plist
driver_service=system/org.pqrs.Karabiner-VirtualHIDDevice-Daemon
driver_daemon_info="/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/Info.plist"
driver_manager=/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager
driver_signer="Developer ID Installer: Fumihiko Takayama (G43BCU2T37)"
karabiner_support="/Library/Application Support/org.pqrs/Karabiner-Elements"
karabiner_agents="$karabiner_support/Karabiner-Elements Non-Privileged Agents v2.app/Contents/MacOS/Karabiner-Elements Non-Privileged Agents v2"
karabiner_daemons="$karabiner_support/Karabiner-Elements Privileged Daemons v2.app/Contents/MacOS/Karabiner-Elements Privileged Daemons v2"

run() {
  if [[ $dry_run -eq 1 ]]; then
    printf 'would run:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

install_daemon() {
  local service=$1 source=$2 destination=$3
  run sudo mkdir -p /usr/local/var/log
  run sudo cp "$source" "$destination"
  if launchctl print "$service" >/dev/null 2>&1; then
    run sudo launchctl bootout "$service"
  fi
  run sudo launchctl bootstrap system "$destination"
}

if [[ ! -x $kanata_src ]]; then
  echo "Homebrew's Kanata is missing. Run: brew install kanata" >&2
  exit 1
fi
kanata_version=$("$kanata_src" --version)

# Each Kanata release speaks one driver IPC; the socket it looks for names it.
if grep -q 'karabiner_virtual_hid_device_service.sock' "$kanata_src"; then
  driver_version=8.0.0
elif grep -q 'vhidd_server' "$kanata_src"; then
  driver_version=6.2.0
else
  echo "Cannot tell which Karabiner driver $kanata_version needs; see Kanata's docs/setup-macos.md." >&2
  exit 1
fi
echo "$kanata_version needs Karabiner driver $driver_version."

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
log_start=0
[[ -f $kanata_log ]] && log_start=$(wc -c <"$kanata_log")
kanata_restart=0
driver_changed=0

installed_kanata=""
[[ -x $kanata_dest ]] && installed_kanata=$("$kanata_dest" --version)
if [[ $installed_kanata != "$kanata_version" ]]; then
  echo "Installing $kanata_version to $kanata_dest."
  run mkdir -p "$HOME/.local/bin"
  run cp "$kanata_src" "$kanata_dest.new"
  # Input Monitoring and Accessibility follow the signing identity, so an upgrade keeps them.
  run codesign --force --sign "Developer ID Application" --identifier com.tylerlaprade.kanata "$kanata_dest.new"
  run mv -f "$kanata_dest.new" "$kanata_dest"
  kanata_restart=1
fi

if [[ -d /Applications/Karabiner-Elements.app || -d $karabiner_support ]]; then
  echo "Removing Karabiner-Elements; Kanata uses only the driver."
  if [[ -x $karabiner_agents ]]; then
    run "$karabiner_agents" unregister-core-agents
    run "$karabiner_agents" unregister-multitouch-extension-agent
  fi
  if [[ -x $karabiner_daemons && $dry_run -eq 1 ]]; then
    run "$karabiner_daemons" unregister-core-daemons
  elif [[ -x $karabiner_daemons ]] && ! "$karabiner_daemons" unregister-core-daemons; then
    for service in system/org.pqrs.service.daemon.Karabiner-Core-Service system/org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon; do
      if launchctl print "$service" >/dev/null 2>&1; then
        sudo launchctl bootout "$service"
      fi
    done
  fi
  run brew uninstall --cask karabiner-elements
  driver_changed=1
fi

installed_driver=""
if [[ -f $driver_daemon_info && $driver_changed -eq 0 ]]; then
  installed_driver=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$driver_daemon_info")
fi
if [[ $installed_driver != "$driver_version" ]]; then
  package="$workdir/Karabiner-DriverKit-VirtualHIDDevice-$driver_version.pkg"
  curl -fsSL -o "$package" "https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice/releases/download/v$driver_version/Karabiner-DriverKit-VirtualHIDDevice-$driver_version.pkg"
  signature=$(pkgutil --check-signature "$package")
  if [[ $signature != *"$driver_signer"* ]] || ! spctl -a -t install "$package" 2>/dev/null; then
    echo "The driver package is not signed by $driver_signer and notarized; not installing it." >&2
    exit 1
  fi
  echo "Installing Karabiner driver $driver_version (signature and notarization checked)."
  run sudo installer -pkg "$package" -target /
  driver_changed=1
fi

if ! cmp -s "$DOTFILES/LaunchDaemons/org.pqrs.Karabiner-VirtualHIDDevice-Daemon.plist" "$driver_plist"; then
  install_daemon "$driver_service" "$DOTFILES/LaunchDaemons/org.pqrs.Karabiner-VirtualHIDDevice-Daemon.plist" "$driver_plist"
  driver_changed=1
elif [[ $driver_changed -eq 1 ]]; then
  run sudo launchctl kickstart -k "$driver_service"
fi

extensions=$(systemextensionsctl list 2>/dev/null)
if [[ $driver_changed -eq 1 || $extensions != *'org.pqrs.Karabiner-DriverKit-VirtualHIDDevice ('*'[activated enabled]'* ]]; then
  echo "Activating the driver."
  run sudo "$driver_manager" activate
  driver_changed=1
  extensions=$(systemextensionsctl list 2>/dev/null)
  if [[ $dry_run -eq 0 && $extensions != *'org.pqrs.Karabiner-DriverKit-VirtualHIDDevice ('*'[activated enabled]'* ]]; then
    echo "macOS has not enabled the driver yet:" >&2
    grep 'org.pqrs' <<<"$extensions" >&2 || true
    echo "Allow it in System Settings → General → Login Items & Extensions → Driver Extensions," >&2
    echo "restart the Mac if it says so, then run kanata-setup again." >&2
    exit 1
  fi
fi

[[ $driver_changed -eq 1 ]] && kanata_restart=1
sed "s|__HOME__|$HOME|g" "$DOTFILES/LaunchDaemons/com.tylerlaprade.kanata.plist" >"$workdir/kanata.plist"
if ! cmp -s "$workdir/kanata.plist" "$kanata_plist"; then
  install_daemon "$kanata_service" "$workdir/kanata.plist" "$kanata_plist"
elif [[ $kanata_restart -eq 1 ]]; then
  run sudo launchctl kickstart -k "$kanata_service"
else
  echo "Kanata is already set up."
  exit 0
fi

if [[ $dry_run -eq 1 ]]; then
  echo "Dry run: nothing changed."
  exit 0
fi

held=0
for _ in $(seq 60); do
  sleep 0.5
  started=$(tail -c +"$((log_start + 1))" "$kanata_log")
  ready=$(grep '^virtual_hid_keyboard_ready' <<<"$started") || ready=""
  if [[ $started == *'releasing input devices'* ]]; then
    break
  fi
  if [[ $started == *'keyboard grabbed'* && ${ready##*$'\n'} == 'virtual_hid_keyboard_ready true' ]]; then
    held=$((held + 1))
    if [[ $held -ge 6 ]]; then
      echo "Kanata is connected to the driver and remapping the keyboard."
      exit 0
    fi
  fi
done
echo "Kanata is not remapping the keyboard. Its log since the restart:" >&2
tail -c +"$((log_start + 1))" "$kanata_log" | grep -v '^connect_failed' | tail -20 >&2
exit 1
