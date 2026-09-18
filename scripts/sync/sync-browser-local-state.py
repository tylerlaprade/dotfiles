#!/usr/bin/python3
# Apple's stable python3 on purpose — see sync-macos-defaults.py shebang note.
"""Bidirectional sync of browser-wide Chromium settings that no other sync carries.

Memory Saver, its savings level, Energy Saver, and hardware acceleration are
browser-wide, so Brave and Chrome keep them in `Local State` instead of the
profile. Brave Sync only moves profile data and sync-macos-defaults.py only
moves `defaults` domains, so a change made in the settings UI stayed on one
machine. This script tracks a whitelist of `Local State` paths in one JSON file
per browser under scripts/setup/browser-local-state/, merged per key the same
way as sync-macos-defaults.py (see threeway.py).

A running browser holds `Local State` in memory and rewrites the whole file
from memory, so an edit made while it runs is lost. Repo values are applied
only while the browser is closed; while it runs, the base is left untouched
so the next run tries again. To apply a pulled change now, quit the
browser, run `sync-dotfiles`, and reopen it.

Called by sync-dotfiles.sh (LaunchAgent: at login and daily).
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import threeway  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
STATE_DIR = os.path.join(REPO_ROOT, "scripts", "setup", "browser-local-state")

APP_SUPPORT = os.path.expanduser("~/Library/Application Support")
BROWSERS = {
    "brave": {
        "local_state": os.path.join(APP_SUPPORT, "BraveSoftware", "Brave-Browser", "Local State"),
        "process": "Brave Browser",
    },
    "chrome": {
        "local_state": os.path.join(APP_SUPPORT, "Google", "Chrome", "Local State"),
        "process": "Google Chrome",
    },
}

TRACKED_PATHS = [
    "performance_tuning.high_efficiency_mode",
    "performance_tuning.battery_saver_mode",
    "hardware_acceleration_mode.enabled",
]

def get_path(tree, dotted):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(tree, dotted, value):
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def read_local_state(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def write_local_state(path, tree):
    directory = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as tmp:
        json.dump(tree, tmp, separators=(",", ":"))
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def export_entries(tree):
    entries = {}
    for dotted in TRACKED_PATHS:
        value = get_path(tree, dotted)
        if value is not None:
            entries[dotted] = value
    return entries


def repo_path(browser):
    return os.path.join(STATE_DIR, f"{browser}.json")


def read_repo(browser):
    path = repo_path(browser)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def write_repo(browser, entries):
    with open(repo_path(browser), "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)
        f.write("\n")


def browser_running(process_name):
    return subprocess.run(["pgrep", "-xq", process_name]).returncode == 0


def apply_entries(browser, config, updates):
    if browser_running(config["process"]):
        print(f"ℹ️  {browser}: repo settings differ but {config['process']} is running; "
              "quit it and run sync-dotfiles to apply")
        return False
    tree = read_local_state(config["local_state"])
    for dotted, value in updates.items():
        set_path(tree, dotted, value)
    write_local_state(config["local_state"], tree)
    threeway.log_applied("browser-local-state", browser, updates)
    print(f"✅ {browser}: applied {', '.join(sorted(updates))}")
    return True


for browser, config in BROWSERS.items():
    tree = read_local_state(config["local_state"])
    repo_entries = read_repo(browser)
    if tree is None and repo_entries is None:
        continue

    local_entries = export_entries(tree) if tree is not None else None
    base_name = os.path.join("browser-local-state", browser)

    if repo_entries is not None and local_entries is not None:
        base = threeway.load_base(base_name)
        merged = threeway.merge(base, local_entries, repo_entries)
        updates, _ = threeway.changes(local_entries, merged)
        if updates and not apply_entries(browser, config, updates):
            continue
        if merged != repo_entries:
            write_repo(browser, merged)
        threeway.save_base(base_name, merged)
    elif local_entries:
        write_repo(browser, local_entries)
        threeway.save_base(base_name, local_entries)
