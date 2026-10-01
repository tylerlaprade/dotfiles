#!/usr/bin/python3
# Apple's stable python3 on purpose — see sync-macos-defaults.py shebang note.
"""Apply macOS defaults on a fresh machine.

Adopts scripts/setup/macos-defaults/*.json through the sync engine (repo wins
everywhere, and that becomes the sync base), then applies the settings that
live outside the defaults domains. Run on a new machine after install.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).absolute().parent
DOMAIN_DIR = SCRIPT_DIR / "macos-defaults"
SYNC_SCRIPT = SCRIPT_DIR / ".." / "sync" / "sync-macos-defaults.py"

# pmset lives outside the defaults domains; -c scopes a setting to power adapter.
POWER_ADAPTER_SETTINGS = [
    ("sleep", "0"),  # System Settings: "Prevent automatic sleeping on power adapter when the display is off"
]

if not DOMAIN_DIR.exists():
    print("No snapshot directory found at", DOMAIN_DIR)
    sys.exit(1)

failed: list[tuple[list[str], str]] = []


def run_defaults(args: list[str]) -> None:
    """Run a defaults command. No sudo retry: defaults run as root writes to
    root's preference domain, not the user's."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        failed.append((args, result.stderr.strip()))


run_defaults([str(SYNC_SCRIPT), "--adopt"])

for setting, value in POWER_ADAPTER_SETTINGS:
    run_defaults(["sudo", "pmset", "-c", setting, value])

# Restart affected services
for proc in ["Dock", "Finder", "SystemUIServer"]:
    subprocess.run(["killall", proc], stderr=subprocess.DEVNULL, check=False)

if failed:
    print(f"\n{len(failed)} setting(s) failed:")
    for args, err in failed:
        print(f"  {' '.join(args[1:4])}: {err}")
else:
    print("macOS defaults applied.")
