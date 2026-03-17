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

for domain, entries in snapshot.items():
    for key, info in entries.items():
        t = info["type"]
        val = info.get("value")
        if t == "plist-file":
            plist_path = os.path.join(SCRIPT_DIR, info["file"])
            subprocess.run(["defaults", "import", domain, plist_path])
        elif t == "bool":
            subprocess.run(["defaults", "write", domain, key, "-bool", str(val).lower()])
        elif t == "int":
            subprocess.run(["defaults", "write", domain, key, "-int", str(val)])
        elif t == "float":
            subprocess.run(["defaults", "write", domain, key, "-float", str(val)])
        elif t == "string":
            subprocess.run(["defaults", "write", domain, key, "-string", val])
        elif t == "plist":
            with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
                plistlib.dump({key: val}, tmp, fmt=plistlib.FMT_XML)
                tmp_path = tmp.name
            subprocess.run(["defaults", "import", domain, tmp_path])
            os.unlink(tmp_path)

# Restart affected services
for proc in ["Dock", "Finder", "SystemUIServer"]:
    subprocess.run(["killall", proc], stderr=subprocess.DEVNULL)

print("macOS defaults applied.")
