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

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import threeway

SCRIPT_DIR = Path(__file__).absolute().parent

REPO_ROOT = SCRIPT_DIR.parent.parent
STATE_DIR = REPO_ROOT / "scripts" / "setup" / "browser-local-state"

APP_SUPPORT = Path("~/Library/Application Support").expanduser()


@dataclass(frozen=True)
class Browser:
    local_state: Path
    process: str


BROWSERS = {
    "brave": Browser(
        local_state=APP_SUPPORT / "BraveSoftware" / "Brave-Browser" / "Local State",
        process="Brave Browser",
    ),
    "chrome": Browser(
        local_state=APP_SUPPORT / "Google" / "Chrome" / "Local State",
        process="Google Chrome",
    ),
}

TRACKED_PATHS = [
    "performance_tuning.high_efficiency_mode",
    "performance_tuning.battery_saver_mode",
    "hardware_acceleration_mode.enabled",
]


def get_path(tree: threeway.JSONObject, dotted: str) -> threeway.JSONValue:
    node: threeway.JSONValue = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(tree: threeway.JSONObject, dotted: str, value: threeway.JSONValue) -> None:
    *parents, leaf = dotted.split(".")
    node = tree
    for part in parents:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[leaf] = value


def write_local_state(path: Path, tree: threeway.JSONObject) -> None:
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        json.dump(tree, tmp, separators=(",", ":"))
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def export_entries(tree: threeway.JSONObject) -> threeway.JSONObject:
    entries: threeway.JSONObject = {}
    for dotted in TRACKED_PATHS:
        value = get_path(tree, dotted)
        if value is not None:
            entries[dotted] = value
    return entries


def repo_path(browser: str) -> Path:
    return STATE_DIR / f"{browser}.json"


def write_repo(browser: str, entries: threeway.JSONObject) -> None:
    threeway.write_json(repo_path(browser), entries, sort_keys=True)


def browser_running(process_name: str) -> bool:
    return subprocess.run(["pgrep", "-xq", process_name], check=False).returncode == 0


def apply_entries(browser: str, config: Browser, updates: threeway.JSONObject) -> bool:
    if browser_running(config.process):
        print(f"\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}  {browser}: repo settings differ but {config.process} is running; "
              "quit it and run sync-dotfiles to apply")
        return False
    tree = threeway.read_json_object(config.local_state)
    if tree is None:
        print(f"{browser}: {config.local_state} is not a JSON object; leaving it alone")
        return False
    for dotted, value in updates.items():
        set_path(tree, dotted, value)
    write_local_state(config.local_state, tree)
    threeway.log_applied("browser-local-state", browser, updates)
    print(f"✅ {browser}: applied {', '.join(sorted(updates))}")
    return True


for browser, config in BROWSERS.items():
    tree = threeway.read_json_object(config.local_state)
    repo_entries = threeway.read_json_object(repo_path(browser))
    if tree is None and repo_entries is None:
        continue

    local_entries = export_entries(tree) if tree is not None else None
    base_name = f"browser-local-state/{browser}"

    if repo_entries is not None and local_entries is not None:
        base = threeway.load_base(base_name)
        if base is None:
            merged = threeway.merge_unbased(local_entries, repo_entries)
        else:
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
