#!/usr/bin/env -S uv run --script
"""Capture current macOS defaults to a snapshot file.

Called by sync-dotfiles.sh on each session start. Reads macos-defaults.conf
for configuration, exports current values to macos-defaults.json.

Config rules:
  - +domain: whitelist an Apple domain (com.apple.* are excluded by default)
  - !domain: blacklist a non-Apple domain (non-Apple are included by default)
  - domain !pat1 !pat2: per-key blacklist for an included domain
  - NSGlobalDomain: always included (special case)
"""

import fnmatch
import json
import os
import plistlib
import subprocess
import sys
from datetime import date, datetime
from math import isclose

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.conf")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.json")

if not os.path.exists(CONF_PATH):
    sys.exit(0)


def load_existing_snapshot():
    if not os.path.exists(SNAPSHOT_PATH):
        return {}
    try:
        with open(SNAPSHOT_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

# Parse conf
apple_whitelist = {}  # domain -> key blacklist patterns
domain_blacklist = set()  # domains to exclude
key_blacklists = {}  # domain -> key blacklist patterns (for non-Apple overrides)
global_key_blacklist = []  # patterns applied to all domains

with open(CONF_PATH) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Split on " !" to separate domain from key blacklist patterns
        # This allows domain names with spaces (e.g. "Avatar Cache Index")
        if " !" in line:
            domain_part = line[: line.index(" !")]
            pattern_part = line[line.index(" !") :]
            patterns = [p.strip().lstrip("!") for p in pattern_part.split(" !") if p.strip()]
        else:
            domain_part = line
            patterns = []

        if domain_part == "*":
            # Global key blacklist (applies to all domains)
            global_key_blacklist.extend(patterns)
        elif domain_part.startswith("+"):
            # Apple domain whitelist
            apple_whitelist[domain_part[1:]] = patterns
        elif domain_part.startswith("!"):
            # Non-Apple domain blacklist
            domain_blacklist.add(domain_part[1:])
        else:
            # Per-key blacklist for an auto-included domain
            key_blacklists[domain_part] = patterns

# Get all domains
raw = subprocess.run(["defaults", "domains"], capture_output=True, text=True)
all_domains = [d.strip() for d in raw.stdout.split(",")]

# Build final domain list
domains_to_export = {}

for domain in all_domains:
    if domain.startswith("com.apple."):
        # Apple: only include if whitelisted
        if domain in apple_whitelist:
            domains_to_export[domain] = apple_whitelist[domain]
    else:
        # Non-Apple: include unless blacklisted
        if domain not in domain_blacklist:
            domains_to_export[domain] = key_blacklists.get(domain, [])

# Always include whitelisted domains that aren't in `defaults domains`
# (e.g. com.apple.LaunchServices/com.apple.launchservices.secure has a slash)
for domain, patterns in apple_whitelist.items():
    if domain not in domains_to_export:
        domains_to_export[domain] = patterns

# NSGlobalDomain doesn't appear in `defaults domains` output
if "NSGlobalDomain" in apple_whitelist:
    domains_to_export["NSGlobalDomain"] = apple_whitelist["NSGlobalDomain"]


def has_bytes(obj):
    """Check if a nested structure contains bytes objects."""
    if isinstance(obj, bytes):
        return True
    if isinstance(obj, dict):
        return any(has_bytes(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_bytes(v) for v in obj)
    return False


def normalize_plist_value(obj):
    """Convert plist-only values into JSON-safe structures."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: normalize_plist_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_plist_value(v) for v in obj]
    return obj


def stabilize_number(domain, key, kind, value, existing_snapshot):
    """Preserve prior numeric typing/value when a new export is effectively identical."""
    existing = existing_snapshot.get(domain, {}).get(key)
    if not existing or existing.get("type") not in {"int", "float"}:
        return kind, value

    old_type = existing["type"]
    old_value = existing.get("value")
    if not isinstance(old_value, (int, float)):
        return kind, value

    if old_type == kind and old_value == value:
        return old_type, old_value

    if isclose(float(old_value), float(value), rel_tol=1e-9, abs_tol=1e-8):
        return old_type, old_value

    return kind, value


def export_domain(domain, blacklist, existing_snapshot):
    """Export a single domain, returning (domain, entries) or None."""
    raw = subprocess.run(["defaults", "export", domain, "-"], capture_output=True)
    if raw.returncode != 0:
        return None
    try:
        d = plistlib.loads(raw.stdout)
    except Exception:
        return None

    entries = {}
    for key in sorted(d.keys()):
        if any(fnmatch.fnmatch(key, pat) for pat in blacklist):
            continue
        val = d[key]
        if isinstance(val, bool):
            entries[key] = {"type": "bool", "value": val}
        elif isinstance(val, int):
            kind, value = stabilize_number(domain, key, "int", val, existing_snapshot)
            entries[key] = {"type": kind, "value": value}
        elif isinstance(val, float):
            kind, value = stabilize_number(domain, key, "float", val, existing_snapshot)
            entries[key] = {"type": kind, "value": value}
        elif isinstance(val, str):
            entries[key] = {"type": "string", "value": val}
        elif isinstance(val, (list, dict)):
            if has_bytes(val):
                # Export as raw plist file (bytes can't go in JSON)
                plist_dir = os.path.join(SCRIPT_DIR, "macos-plists")
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

    if entries:
        return (domain, entries)
    return None


existing_snapshot = load_existing_snapshot()
snapshot = {}
for domain, blacklist in sorted(domains_to_export.items()):
    result = export_domain(domain, global_key_blacklist + blacklist, existing_snapshot)
    if result:
        snapshot[result[0]] = result[1]

with open(SNAPSHOT_PATH, "w") as f:
    json.dump(snapshot, f, indent=2)
    f.write("\n")

# Capture pmset (power management) settings
PMSET_PATH = os.path.join(SCRIPT_DIR, "pmset.json")
pmset_snapshot = {}
for source, flag in [("battery", "-b"), ("ac", "-c")]:
    raw = subprocess.run(["pmset", "-g", "custom"], capture_output=True, text=True)
    if raw.returncode != 0:
        continue
    current_source = None
    for line in raw.stdout.splitlines():
        if "Battery Power:" in line:
            current_source = "battery"
            pmset_snapshot.setdefault("battery", {})
        elif "AC Power:" in line:
            current_source = "ac"
            pmset_snapshot.setdefault("ac", {})
        elif current_source and line.strip():
            parts = line.strip().split()
            if len(parts) >= 2:
                # Value is always last token, key is everything before it
                val = parts[-1]
                key = " ".join(parts[:-1])
                try:
                    pmset_snapshot[current_source][key] = int(val)
                except ValueError:
                    pmset_snapshot[current_source][key] = val
    break  # pmset -g custom shows both in one call

with open(PMSET_PATH, "w") as f:
    json.dump(pmset_snapshot, f, indent=2)
    f.write("\n")

# Capture login items
LOGIN_ITEMS_PATH = os.path.join(SCRIPT_DIR, "login-items.json")
raw = subprocess.run(
    ["osascript", "-e", 'tell application "System Events" to get the {name, path} of every login item'],
    capture_output=True, text=True
)
if raw.returncode == 0 and raw.stdout.strip():
    # Output is "name1, name2, path1, path2" — split in half
    parts = [p.strip() for p in raw.stdout.strip().split(", ")]
    half = len(parts) // 2
    names = parts[:half]
    paths = parts[half:]
    items = [{"name": n, "path": p} for n, p in zip(names, paths)]
    with open(LOGIN_ITEMS_PATH, "w") as f:
        json.dump(items, f, indent=2)
        f.write("\n")
