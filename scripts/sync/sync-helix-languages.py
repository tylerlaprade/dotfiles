#!/usr/bin/env -S uv run --script
"""Bidirectional sync of Helix languages.toml with local-only secrets.

The repo copy contains a SOURCERY_TOKEN placeholder. The live copy has the real
token injected from ~/.config/sourcery/auth.yaml. On sync, non-secret edits
flow both ways by a three-way comparison against the last synced text (see
threeway.py), while the token never touches the repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import threeway

repo_path, local_path = Path(sys.argv[1]), Path(sys.argv[2])

PLACEHOLDER = "SOURCERY_TOKEN"
AUTH_YAML = Path("~/.config/sourcery/auth.yaml").expanduser()


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def get_token() -> str:
    """Read the Sourcery token from auth.yaml."""
    text = read(AUTH_YAML)
    for line in text.splitlines():
        if line.startswith("sourcery_token:"):
            return line.split(":", 1)[1].strip()
    return ""


def redact(content: str, token: str) -> str:
    """Replace real token with placeholder."""
    if token:
        return content.replace(token, PLACEHOLDER)
    return content


def inject(content: str, token: str) -> str:
    """Replace placeholder with real token."""
    if token:
        return content.replace(PLACEHOLDER, token)
    return content


token = get_token()
repo_content = read(repo_path)
local_content = read(local_path)

local_redacted = redact(local_content, token) if local_content else ""
base = threeway.load_base("helix-languages")
# The text is wrapped in a dict, and a dict holding "" is still truthy, so
# merge_unbased() would adopt an empty repo file and wipe the live one.
if base is None and repo_content:
    merged = repo_content
elif base is None:
    merged = local_redacted
else:
    merged = threeway.merge(threeway.strings(base), {"text": local_redacted}, {"text": repo_content})["text"]

if merged and merged != repo_content:
    write(repo_path, merged)

# Always write live file with real token
live_content = inject(merged, token)
if live_content != local_content:
    threeway.log_applied("helix-languages", str(local_path), {"text": "updated from repo"})
    write(local_path, live_content)

threeway.save_base("helix-languages", {"text": merged})
