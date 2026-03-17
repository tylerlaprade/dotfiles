#!/usr/bin/env -S uv run --script
"""Bidirectional sync of Graphite preferences.

Merges preferences between the repo file and local user_config,
keeping authToken and alternativeProfiles local-only.

Usage: sync-graphite.py <repo_prefs> <local_config>
"""

import json
import sys

prefs_path, config_path = sys.argv[1], sys.argv[2]

with open(prefs_path) as f:
    prefs = json.load(f)
with open(config_path) as f:
    config = json.load(f)

# Sync FROM local: extract non-secret preferences from config back to repo
local_prefs = {
    k: v for k, v in config.items() if k not in ("authToken", "alternativeProfiles")
}

# Sync TO local: merge repo preferences into config, preserving auth
for k, v in prefs.items():
    if k not in config:
        config[k] = v

# If local has changed preferences, update repo
if local_prefs != prefs:
    with open(prefs_path, "w") as f:
        json.dump(local_prefs, f, indent=2)
        f.write("\n")

# Write merged config back
with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
