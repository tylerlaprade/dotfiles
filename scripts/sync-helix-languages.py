#!/usr/bin/env -S uv run --script
"""Bidirectional sync of Helix languages.toml with local-only secrets.

The repo copy contains a SOURCERY_TOKEN placeholder. The live copy has the real
token injected from ~/.config/sourcery/auth.yaml. On sync, non-secret edits
flow both ways using last-writer-wins (mtime), while the token never touches
the repo.
"""

import os
import re
import sys

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

repo_mtime = os.path.getmtime(repo_path) if os.path.exists(repo_path) else 0
local_mtime = os.path.getmtime(local_path) if os.path.exists(local_path) else 0

if local_content:
    local_redacted = redact(local_content, token)
else:
    local_redacted = ""

if local_mtime > repo_mtime and local_redacted and local_redacted != repo_content:
    # Local is newer — push non-secret changes back to repo
    write(repo_path, local_redacted)
    repo_content = local_redacted

# Always write live file with real token
live_content = inject(repo_content, token)
if live_content != local_content:
    write(local_path, live_content)
