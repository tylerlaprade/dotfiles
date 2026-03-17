#!/usr/bin/env -S uv run --script
"""Bidirectional sync of Claude plugin marketplace sources.

Compares repo and local known_marketplaces.json, using a manifest to detect
deletions. Outputs actions to stdout for the shell caller to execute:
  install <repo-url>
  remove <name>

Also updates the repo file with any locally-added marketplaces and writes
the manifest for next sync.

Usage: sync-claude-plugins.py <repo_file> <local_file> <manifest_file>
  If local_file doesn't exist, outputs install actions for all repo entries.
"""

import json
import os
import sys

repo_path, local_path, manifest_path = sys.argv[1], sys.argv[2], sys.argv[3]

with open(repo_path) as f:
    repo = json.load(f)

if os.path.exists(local_path):
    with open(local_path) as f:
        local = json.load(f)

    synced = set()
    if os.path.exists(manifest_path):
        synced = set(open(manifest_path).read().split())

    for k, v in list(repo.items()):
        if k not in local:
            if k in synced:
                del repo[k]  # was synced before, now deleted locally
                print(f"remove {k}")
            else:
                print(f'install {v["source"]["repo"]}')  # new from another machine

    for k, v in local.items():
        if k not in repo:
            repo[k] = {"source": v["source"]}

    with open(repo_path, "w") as f:
        json.dump(repo, f, indent=2)
        f.write("\n")
else:
    # No local file — install all repo marketplaces from scratch
    local = {}
    for v in repo.values():
        print(f'install {v["source"]["repo"]}')

# Update manifest with current local state
if os.path.exists(local_path):
    with open(local_path) as f:
        local = json.load(f)
    with open(manifest_path, "w") as f:
        f.write("\n".join(local.keys()))
