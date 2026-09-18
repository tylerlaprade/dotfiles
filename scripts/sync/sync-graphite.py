#!/usr/bin/python3
# Apple's stable python3 on purpose — see sync-macos-defaults.py shebang note.
"""Bidirectional sync of Graphite preferences.

Keeps non-secret preferences in sync between the repo file and local
``user_config`` with a per-key three-way merge (see threeway.py), while
preserving ``authToken`` and ``alternativeProfiles`` locally.

Usage: sync-graphite.py <repo_prefs> <local_config>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threeway  # noqa: E402

prefs_path, config_path = sys.argv[1], sys.argv[2]
LOCAL_ONLY_KEYS = {"authToken", "alternativeProfiles"}
LOCAL_ONLY_GTI_KEYS = {"gti.install-uuid"}

with open(prefs_path) as f:
    prefs = json.load(f)
with open(config_path) as f:
    config = json.load(f)

# Split local config into syncable preferences and machine-local state.
local_only = {k: v for k, v in config.items() if k in LOCAL_ONLY_KEYS}
local_prefs = {k: v for k, v in config.items() if k not in LOCAL_ONLY_KEYS}

merged_prefs = threeway.merge(threeway.load_base("graphite"), local_prefs, prefs)

# Strip machine-local gtiConfigs entries before writing to repo.
repo_prefs = {**merged_prefs}
if "gtiConfigs" in repo_prefs:
    repo_prefs["gtiConfigs"] = [
        c for c in repo_prefs["gtiConfigs"] if c.get("key") not in LOCAL_ONLY_GTI_KEYS
    ]

if prefs != repo_prefs:
    with open(prefs_path, "w") as f:
        json.dump(repo_prefs, f, indent=2)
        f.write("\n")

merged_config = {**local_only, **merged_prefs}

if config != merged_config:
    threeway.log_applied("graphite", config_path, *threeway.changes(local_prefs, merged_prefs))
    with open(config_path, "w") as f:
        json.dump(merged_config, f, indent=2)
        f.write("\n")

threeway.save_base("graphite", merged_prefs)
