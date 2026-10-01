#!/usr/bin/python3
# Apple's stable python3 on purpose — see sync-macos-defaults.py shebang note.
"""Bidirectional sync of Graphite preferences.

Keeps non-secret preferences in sync between the repo file and local
``user_config`` with a per-key three-way merge (see threeway.py), while
preserving ``authToken`` and ``alternativeProfiles`` locally.

Usage: sync-graphite.py <repo_prefs> <local_config>
"""

from __future__ import annotations

import sys
from pathlib import Path

import threeway

prefs_path, config_path = Path(sys.argv[1]), Path(sys.argv[2])
LOCAL_ONLY_KEYS = {"authToken", "alternativeProfiles"}
LOCAL_ONLY_GTI_KEYS = {"gti.install-uuid"}


def is_local_only_gti_config(entry: threeway.JSONValue) -> bool:
    return isinstance(entry, dict) and entry.get("key") in LOCAL_ONLY_GTI_KEYS


prefs = threeway.read_json_object(prefs_path)
if prefs is None:
    sys.exit(f"{prefs_path}: expected a JSON object")

created = not config_path.exists()
config = {} if created else threeway.read_json_object(config_path)
if config is None:
    sys.exit(f"{config_path}: expected a JSON object")

# Split local config into syncable preferences and machine-local state.
local_only = {k: v for k, v in config.items() if k in LOCAL_ONLY_KEYS}
local_prefs = {k: v for k, v in config.items() if k not in LOCAL_ONLY_KEYS}

base = threeway.load_base("graphite")
if base is None:
    merged_prefs = threeway.merge_unbased(local_prefs, prefs)
else:
    merged_prefs = threeway.merge(base, local_prefs, prefs)

# Strip machine-local gtiConfigs entries before writing to repo.
repo_prefs = {**merged_prefs}
gti_configs = repo_prefs.get("gtiConfigs")
if isinstance(gti_configs, list):
    repo_prefs["gtiConfigs"] = [c for c in gti_configs if not is_local_only_gti_config(c)]

if prefs != repo_prefs:
    threeway.write_json(prefs_path, repo_prefs)

merged_config = {**local_only, **merged_prefs}

if config != merged_config:
    threeway.log_applied("graphite", str(config_path), *threeway.changes(local_prefs, merged_prefs))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    threeway.write_json(config_path, merged_config)
    if created and "authToken" not in local_only:
        print("\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}  Graphite preferences adopted. Run 'gt auth' to add your auth token.")

threeway.save_base("graphite", merged_prefs)
