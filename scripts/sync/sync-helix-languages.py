#!/usr/bin/env -S uv run --script
"""Bidirectional sync of Helix languages.toml with local-only secrets.

The repo copy contains a SOURCERY_TOKEN placeholder. The live copy has the real
token injected from ~/.config/sourcery/auth.yaml. On sync, non-secret edits
flow both ways by a three-way comparison against the last synced text (see
threeway.py), while the token never touches the repo.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threeway  # noqa: E402

repo_path, local_path = sys.argv[1], sys.argv[2]

PLACEHOLDER = "SOURCERY_TOKEN"
AUTH_YAML = os.path.expanduser("~/.config/sourcery/auth.yaml")


def read(path):
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def get_token():
    """Read the Sourcery token from auth.yaml."""
    text = read(AUTH_YAML)
    for line in text.splitlines():
        if line.startswith("sourcery_token:"):
            return line.split(":", 1)[1].strip()
    return ""


def redact(content, token):
    """Replace real token with placeholder."""
    if token:
        return content.replace(token, PLACEHOLDER)
    return content


def inject(content, token):
    """Replace placeholder with real token."""
    if token:
        return content.replace(PLACEHOLDER, token)
    return content


token = get_token()
repo_content = read(repo_path)
local_content = read(local_path)

local_redacted = redact(local_content, token) if local_content else ""
base = threeway.load_base("helix-languages")
merged = threeway.merge(base, {"text": local_redacted}, {"text": repo_content})["text"]

if merged and merged != repo_content:
    write(repo_path, merged)

# Always write live file with real token
live_content = inject(merged, token)
if live_content != local_content:
    threeway.log_applied("helix-languages", local_path, {"text": "updated from repo"})
    write(local_path, live_content)

threeway.save_base("helix-languages", {"text": merged})
