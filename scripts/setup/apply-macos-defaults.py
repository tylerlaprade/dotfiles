#!/usr/bin/python3
# Apple's stable python3 on purpose — see sync-macos-defaults.py shebang note.
"""Apply macOS defaults on a fresh machine.

Adopts scripts/setup/macos-defaults/*.json through the sync engine (repo wins
everywhere, and that becomes the sync base), then applies the settings that
live outside the defaults domains. Run on a new machine after install.
"""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAIN_DIR = os.path.join(SCRIPT_DIR, "macos-defaults")
SYNC_SCRIPT = os.path.join(SCRIPT_DIR, "..", "sync", "sync-macos-defaults.py")

# pmset lives outside the defaults domains; -c scopes a setting to power adapter.
POWER_ADAPTER_SETTINGS = [
    ("sleep", "0"),  # System Settings: "Prevent automatic sleeping on power adapter when the display is off"
]

if not os.path.exists(DOMAIN_DIR):
    print("No snapshot directory found at", DOMAIN_DIR)
    sys.exit(1)

failed = []


def run_defaults(args):
    """Run a defaults command. No sudo retry: defaults run as root writes to
    root's preference domain, not the user's."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        failed.append((args, result.stderr.strip()))


run_defaults([SYNC_SCRIPT, "--adopt"])

for setting, value in POWER_ADAPTER_SETTINGS:
    run_defaults(["sudo", "pmset", "-c", setting, value])

# Restart affected services
for proc in ["Dock", "Finder", "SystemUIServer"]:
    subprocess.run(["killall", proc], stderr=subprocess.DEVNULL)

if failed:
    print(f"\n{len(failed)} setting(s) failed:")
    for args, err in failed:
        print(f"  {' '.join(args[1:4])}: {err}")
else:
    print("macOS defaults applied.")
