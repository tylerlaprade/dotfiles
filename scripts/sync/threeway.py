"""Three-way merge for the bidirectional syncs.

The base is the state both sides agreed on at the last run. A side that moved
away from the base carries a real change; when both moved, the live machine
wins, because a change made here was made on purpose. Modification times play
no part, so a git checkout, stash, or rebase cannot make the repo look newer
than it is.

With no base, the live machine wins everywhere and nothing is written to it.
A fresh machine adopts instead: the repo wins everywhere and becomes the base.
"""

import json
import os
from datetime import datetime

BASE_DIR = os.path.expanduser("~/.local/state/dotfiles-sync")
LOG_PATH = os.path.expanduser("~/Library/Logs/dotfiles-sync.log")
MISSING = object()


def base_path(name):
    return os.path.join(BASE_DIR, f"{name}.json")


def load_base(name):
    path = base_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError:
        return None


def save_base(name, value):
    path = base_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def merge_value(base, local, repo):
    if local == repo:
        return local
    if local == base:
        return repo
    return local


def merge(base, local, repo, repo_can_delete=True):
    if base is None:
        return dict(local)
    merged = {}
    for key in sorted(set(base) | set(local) | set(repo)):
        value = merge_value(base.get(key, MISSING), local.get(key, MISSING), repo.get(key, MISSING))
        if value is MISSING and not repo_can_delete and key in local:
            value = local[key]
        if value is not MISSING:
            merged[key] = value
    return merged


def changes(local, merged):
    """What the live side must take from the merge: (updates, removals)."""
    updates = {key: value for key, value in merged.items() if local.get(key, MISSING) != value}
    removals = [key for key in local if key not in merged]
    return updates, removals


def log_applied(sync, target, updates, removals=()):
    if not updates and not removals:
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with open(LOG_PATH, "a") as f:
        for key, value in updates.items():
            f.write(f"{stamp} {sync} {target} {key} = {json.dumps(value, sort_keys=True)}\n")
        for key in removals:
            f.write(f"{stamp} {sync} {target} {key} removed\n")
