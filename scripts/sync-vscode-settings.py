#!/usr/bin/env -S uv run --script
"""Bidirectional sync of VS Code settings with local-only secrets.

Keeps non-secret top-level settings in sync between the repo file and the live
VS Code settings file using last-writer-wins semantics. Secret keys remain in a
separate local secrets file and are rehydrated into the live settings file.
"""

import json
import os
import sys

repo_path, local_path, secrets_path = sys.argv[1], sys.argv[2], sys.argv[3]
SECRET_KEYS = {"sourcery.token"}


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


repo_settings = load_json(repo_path)
local_settings = load_json(local_path)
secret_settings = load_json(secrets_path)

local_visible = {k: v for k, v in local_settings.items() if k not in SECRET_KEYS}
local_secrets = {k: v for k, v in local_settings.items() if k in SECRET_KEYS}

repo_mtime = os.path.getmtime(repo_path) if os.path.exists(repo_path) else 0
local_mtime = os.path.getmtime(local_path) if os.path.exists(local_path) else 0

merged_visible = local_visible if local_mtime > repo_mtime else repo_settings
merged_secrets = local_secrets or secret_settings

if repo_settings != merged_visible:
    write_json(repo_path, merged_visible)

if merged_secrets:
    if secret_settings != merged_secrets:
        write_json(secrets_path, merged_secrets)
elif os.path.exists(secrets_path):
    os.remove(secrets_path)

merged_local = {**merged_visible, **merged_secrets}
if local_settings != merged_local:
    write_json(local_path, merged_local)
