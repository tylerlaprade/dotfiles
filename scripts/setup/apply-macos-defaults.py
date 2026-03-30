#!/usr/bin/env -S uv run --script
"""Apply macOS defaults from snapshot.

Reads macos-defaults.json and writes each setting via `defaults write`.
Run on a new machine after install.
"""

import json
import os
import plistlib
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.json")

if not os.path.exists(SNAPSHOT_PATH):
    print("No snapshot found at", SNAPSHOT_PATH)
    sys.exit(1)

with open(SNAPSHOT_PATH) as f:
    snapshot = json.load(f)

failed = []

def run_defaults(args):
    """Run defaults command, retry with sudo on permission failure."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 and "Could not write domain" in result.stderr:
        result = subprocess.run(["sudo"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        failed.append((args, result.stderr.strip()))

for domain, entries in snapshot.items():
    for key, info in entries.items():
        t = info["type"]
        val = info.get("value")
        if t == "plist-file":
            plist_path = os.path.join(SCRIPT_DIR, info["file"])
            run_defaults(["defaults", "import", domain, plist_path])
        elif t == "bool":
            run_defaults(["defaults", "write", domain, key, "-bool", str(val).lower()])
        elif t == "int":
            run_defaults(["defaults", "write", domain, key, "-int", str(val)])
        elif t == "float":
            run_defaults(["defaults", "write", domain, key, "-float", str(val)])
        elif t == "string":
            run_defaults(["defaults", "write", domain, key, "-string", val])
        elif t == "plist":
            with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
                plistlib.dump({key: val}, tmp, fmt=plistlib.FMT_XML)
                tmp_path = tmp.name
            run_defaults(["defaults", "import", domain, tmp_path])
            os.unlink(tmp_path)

# Power management (not in defaults — uses pmset)
pmset_file = os.path.join(SCRIPT_DIR, "pmset.json")
pmset_settings = {}
if os.path.exists(pmset_file):
    with open(pmset_file) as f:
        pmset_settings = json.load(f)
for source, settings in pmset_settings.items():
    flag = "-b" if source == "battery" else "-c"
    for key, val in settings.items():
        subprocess.run(["sudo", "pmset", flag, key, str(val)], capture_output=True)

# Restore login items
login_items_file = os.path.join(SCRIPT_DIR, "login-items.json")
if os.path.exists(login_items_file):
    with open(login_items_file) as f:
        login_items = json.load(f)
    for item in login_items:
        path = os.path.expanduser(item["path"])
        # Skip items whose apps aren't installed
        if not os.path.exists(path):
            failed.append((["login-item", item["name"]], f"App not found at {path}"))
            continue
        subprocess.run([
            "osascript", "-e",
            f'tell application "System Events" to make login item at end with properties {{path:"{path}", hidden:false}}'
        ], capture_output=True)

# Restart affected services
for proc in ["Dock", "Finder", "SystemUIServer"]:
    subprocess.run(["killall", proc], stderr=subprocess.DEVNULL)

if failed:
    print(f"\n{len(failed)} setting(s) failed:")
    for args, err in failed:
        print(f"  {' '.join(args[1:4])}: {err}")
else:
    print("macOS defaults applied.")
