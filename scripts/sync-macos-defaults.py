#!/usr/bin/env -S uv run --script
"""Capture current macOS defaults to a snapshot file.

Called by sync-dotfiles.sh on each session start. Reads macos-defaults.conf
for tracked domains and blacklist patterns, exports current values to
macos-defaults.json.
"""

import fnmatch
import os
import json
import plistlib
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.conf")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.json")

if not os.path.exists(CONF_PATH):
    sys.exit(0)

# Parse conf: domain -> list of blacklist patterns
domains = {}
with open(CONF_PATH) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        domain = parts[0]
        blacklist = [p[1:] for p in parts[1:] if p.startswith("!")]
        domains[domain] = blacklist

snapshot = {}

for domain, blacklist in domains.items():
    raw = subprocess.run(["defaults", "export", domain, "-"], capture_output=True)
    if raw.returncode != 0:
        continue
    d = plistlib.loads(raw.stdout)

    entries = {}
    for key in sorted(d.keys()):
        if any(fnmatch.fnmatch(key, pat) for pat in blacklist):
            continue
        val = d[key]
        if isinstance(val, bool):
            entries[key] = {"type": "bool", "value": val}
        elif isinstance(val, int):
            entries[key] = {"type": "int", "value": val}
        elif isinstance(val, float):
            entries[key] = {"type": "int" if val == int(val) else "float", "value": int(val) if val == int(val) else val}
        elif isinstance(val, str):
            entries[key] = {"type": "string", "value": val}
        elif isinstance(val, (list, dict)):
            entries[key] = {"type": "plist", "value": val}

    if entries:
        snapshot[domain] = entries

with open(SNAPSHOT_PATH, "w") as f:
    json.dump(snapshot, f, indent=2)
    f.write("\n")
