#!/usr/bin/env -S uv run --script
"""Bidirectional sync of macOS defaults using per-domain files.

Each tracked domain gets its own JSON file under scripts/setup/macos-defaults/.
Uses last-writer-wins (mtime) to decide direction per domain, matching the
pattern used by sync-graphite.py and sync-vscode-settings.py.

Called by sync-dotfiles.sh on each session start.
"""

import fnmatch
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from datetime import date, datetime
from math import isclose

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
SETUP_DIR = os.path.join(REPO_ROOT, "scripts", "setup")
CONF_PATH = os.path.join(SETUP_DIR, "macos-defaults.conf")
DOMAIN_DIR = os.path.join(SETUP_DIR, "macos-defaults")

if not os.path.exists(CONF_PATH):
    sys.exit(0)

os.makedirs(DOMAIN_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Config parsing (unchanged from before)
# ---------------------------------------------------------------------------

apple_whitelist = {}
domain_blacklist = set()
key_blacklists = {}
global_key_blacklist = []

with open(CONF_PATH) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if " !" in line:
            domain_part = line[: line.index(" !")]
            pattern_part = line[line.index(" !"):]
            patterns = [p.strip().lstrip("!") for p in pattern_part.split(" !") if p.strip()]
        else:
            domain_part = line
            patterns = []

        if domain_part == "*":
            global_key_blacklist.extend(patterns)
        elif domain_part.startswith("+"):
            apple_whitelist[domain_part[1:]] = patterns
        elif domain_part.startswith("!"):
            domain_blacklist.add(domain_part[1:])
        else:
            if domain_part in apple_whitelist:
                apple_whitelist[domain_part].extend(patterns)
            else:
                key_blacklists.setdefault(domain_part, []).extend(patterns)

# Build domain list
raw = subprocess.run(["defaults", "domains"], capture_output=True, text=True)
all_domains = [d.strip() for d in raw.stdout.split(",")]

domains_to_export = {}
for domain in all_domains:
    if domain.startswith("com.apple."):
        if domain in apple_whitelist:
            domains_to_export[domain] = apple_whitelist[domain]
    else:
        if domain not in domain_blacklist:
            domains_to_export[domain] = key_blacklists.get(domain, [])

for domain, patterns in apple_whitelist.items():
    if domain not in domains_to_export:
        domains_to_export[domain] = patterns

if "NSGlobalDomain" in apple_whitelist:
    domains_to_export["NSGlobalDomain"] = apple_whitelist["NSGlobalDomain"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def has_bytes(obj):
    if isinstance(obj, bytes):
        return True
    if isinstance(obj, dict):
        return any(has_bytes(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_bytes(v) for v in obj)
    return False


def normalize_plist_value(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: normalize_plist_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_plist_value(v) for v in obj]
    return obj


def stabilize_number(domain, key, kind, value, existing):
    """Preserve prior numeric typing/value when effectively identical."""
    entry = existing.get(key)
    if not entry or entry.get("type") not in {"int", "float"}:
        return kind, value

    old_type = entry["type"]
    old_value = entry.get("value")
    if not isinstance(old_value, (int, float)):
        return kind, value

    if old_type == kind and old_value == value:
        return old_type, old_value

    if isclose(float(old_value), float(value), rel_tol=1e-9, abs_tol=1e-8):
        return old_type, old_value

    return kind, value


def export_domain(domain, blacklist, existing):
    """Export a single domain from system, returning entries dict or None."""
    raw = subprocess.run(["defaults", "export", domain, "-"], capture_output=True)
    if raw.returncode != 0:
        return None
    try:
        d = plistlib.loads(raw.stdout)
    except Exception:
        return None

    plist_dir = os.path.join(SETUP_DIR, "macos-plists")
    entries = {}
    for key in sorted(d.keys()):
        if any(fnmatch.fnmatch(key, pat) for pat in blacklist):
            continue
        val = d[key]
        if isinstance(val, bool):
            entries[key] = {"type": "bool", "value": val}
        elif isinstance(val, int):
            kind, value = stabilize_number(domain, key, "int", val, existing)
            entries[key] = {"type": kind, "value": value}
        elif isinstance(val, float):
            kind, value = stabilize_number(domain, key, "float", val, existing)
            entries[key] = {"type": kind, "value": value}
        elif isinstance(val, str):
            entries[key] = {"type": "string", "value": val}
        elif isinstance(val, (list, dict)):
            if has_bytes(val):
                os.makedirs(plist_dir, exist_ok=True)
                plist_file = os.path.join(plist_dir, f"{domain}.{key}.plist")
                with open(plist_file, "wb") as pf:
                    plistlib.dump({key: val}, pf, fmt=plistlib.FMT_XML)
                entries[key] = {"type": "plist-file", "file": f"macos-plists/{domain}.{key}.plist"}
            else:
                entries[key] = {"type": "plist", "value": normalize_plist_value(val)}
        elif isinstance(val, datetime):
            entries[key] = {"type": "datetime", "value": val.isoformat()}
        elif isinstance(val, date):
            entries[key] = {"type": "date", "value": val.isoformat()}
        elif isinstance(val, bytes):
            continue

    return entries if entries else None


def run_defaults(args):
    """Run defaults command, retry with sudo on permission failure."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 and "Could not write domain" in result.stderr:
        result = subprocess.run(["sudo"] + args, capture_output=True, text=True)
    return result.returncode == 0


def apply_domain(domain, entries):
    """Write entries to system via `defaults write`."""
    for key, info in entries.items():
        t = info["type"]
        val = info.get("value")
        if t == "plist-file":
            plist_path = os.path.join(SETUP_DIR, info["file"])
            run_defaults(["defaults", "import", domain, plist_path])
        elif t == "bool":
            run_defaults(["defaults", "write", domain, key, "-bool", str(val).lower()])
        elif t == "int":
            run_defaults(["defaults", "write", domain, key, "-int", str(val)])
        elif t == "float":
            run_defaults(["defaults", "write", domain, key, "-float", str(val)])
        elif t == "string":
            run_defaults(["defaults", "write", domain, key, "-string", val])
        elif t == "plist":
            with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
                plistlib.dump({key: val}, tmp, fmt=plistlib.FMT_XML)
                tmp_path = tmp.name
            run_defaults(["defaults", "import", domain, tmp_path])
            os.unlink(tmp_path)


def domain_path(domain):
    return os.path.join(DOMAIN_DIR, f"{domain}.json")


def read_domain_file(domain):
    path = domain_path(domain)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def write_domain_file(domain, entries):
    path = domain_path(domain)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Bidirectional sync — per-domain, mtime-based newer-wins
# ---------------------------------------------------------------------------

MARKER_DIR = os.path.join(DOMAIN_DIR, ".sync-markers")
os.makedirs(MARKER_DIR, exist_ok=True)

applied_domains = set()

for domain in sorted(domains_to_export):
    blacklist = global_key_blacklist + domains_to_export[domain]
    repo_file = domain_path(domain)
    repo_entries = read_domain_file(domain)
    repo_mtime = os.path.getmtime(repo_file) if os.path.exists(repo_file) else 0

    local_entries = export_domain(domain, blacklist, repo_entries or {})
    if local_entries is None and repo_entries is None:
        continue

    marker = os.path.join(MARKER_DIR, domain)
    marker_mtime = os.path.getmtime(marker) if os.path.exists(marker) else 0

    if repo_entries is not None and local_entries is not None:
        if repo_entries == local_entries:
            pass  # In sync
        elif repo_mtime > marker_mtime:
            # Repo file updated (git pull) since last sync — apply repo values
            apply_domain(domain, repo_entries)
            applied_domains.add(domain)
            # Re-export to capture merged state
            local_entries = export_domain(domain, blacklist, repo_entries)
            if local_entries:
                write_domain_file(domain, local_entries)
        else:
            # Local system changed — export
            write_domain_file(domain, local_entries)
    elif local_entries is not None:
        # New domain, no repo file yet
        write_domain_file(domain, local_entries)
    # else: domain in repo but app not installed — preserve file

    # Touch marker
    with open(marker, "w") as f:
        pass

# Restart affected services if we applied anything
if applied_domains:
    needs_restart = set()
    for domain in applied_domains:
        if "dock" in domain.lower():
            needs_restart.add("Dock")
        if "finder" in domain.lower():
            needs_restart.add("Finder")
        if "systemuiserver" in domain.lower():
            needs_restart.add("SystemUIServer")
    for proc in needs_restart:
        subprocess.run(["killall", proc], stderr=subprocess.DEVNULL)

# ---------------------------------------------------------------------------
# Login items — merge with on-disk file to preserve items from other machines
# ---------------------------------------------------------------------------

LOGIN_ITEMS_PATH = os.path.join(SETUP_DIR, "login-items.json")
raw = subprocess.run(
    ["osascript", "-e", 'tell application "System Events" to get the {name, path} of every login item'],
    capture_output=True, text=True
)
current_items = []
if raw.returncode == 0 and raw.stdout.strip():
    parts = [p.strip() for p in raw.stdout.strip().split(", ")]
    half = len(parts) // 2
    names = parts[:half]
    paths = parts[half:]
    home = os.path.expanduser("~")
    current_items = [
        {"name": n, "path": p.replace(home, "~", 1) if p.startswith(home) else p}
        for n, p in zip(names, paths)
    ]

existing_items = []
try:
    if os.path.exists(LOGIN_ITEMS_PATH):
        with open(LOGIN_ITEMS_PATH) as f:
            existing_items = json.load(f)
except Exception:
    pass

seen_names = {item["name"] for item in current_items}
merged = list(current_items)
for item in existing_items:
    if item["name"] not in seen_names:
        merged.append(item)
        seen_names.add(item["name"])

with open(LOGIN_ITEMS_PATH, "w") as f:
    json.dump(merged, f, indent=2)
    f.write("\n")
