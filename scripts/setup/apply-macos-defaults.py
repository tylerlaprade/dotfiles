#!/usr/bin/env -S uv run --script
"""Apply macOS defaults from per-domain snapshot files.

Reads scripts/setup/macos-defaults/*.json and writes each setting via
`defaults write`. Run on a new machine after install.
"""

import json
import os
import plistlib
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAIN_DIR = os.path.join(SCRIPT_DIR, "macos-defaults")

PER_HOST_SETTINGS = [
    # (domain, key, type_flag, value_str)
    ("com.apple.screensaver", "idleTime", "-int", "600"),
]

if not os.path.exists(DOMAIN_DIR):
    print("No snapshot directory found at", DOMAIN_DIR)
    sys.exit(1)

failed = []


def run_defaults(args):
    """Run defaults command, retry with sudo on permission failure."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 and "Could not write domain" in result.stderr:
        result = subprocess.run(["sudo"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        failed.append((args, result.stderr.strip()))


for filename in sorted(os.listdir(DOMAIN_DIR)):
    if not filename.endswith(".json"):
        continue
    domain = filename[:-5]  # strip .json

    with open(os.path.join(DOMAIN_DIR, filename)) as f:
        entries = json.load(f)

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

for domain, key, type_flag, value in PER_HOST_SETTINGS:
    run_defaults(["defaults", "-currentHost", "write", domain, key, type_flag, value])

# Restore login items
login_items_file = os.path.join(SCRIPT_DIR, "login-items.json")
if os.path.exists(login_items_file):
    with open(login_items_file) as f:
        login_items = json.load(f)
    for item in login_items:
        path = os.path.expanduser(item["path"])
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
