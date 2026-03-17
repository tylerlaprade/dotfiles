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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.conf")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "macos-defaults.json")

if not os.path.exists(CONF_PATH):
    sys.exit(0)

# Parse conf
apple_whitelist = {}  # domain -> key blacklist patterns
domain_blacklist = set()  # domains to exclude
key_blacklists = {}  # domain -> key blacklist patterns (for non-Apple overrides)

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

        if domain_part.startswith("+"):
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


def export_domain(domain, blacklist):
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
            entries[key] = {"type": "int", "value": val}
        elif isinstance(val, float):
            if val == int(val):
                entries[key] = {"type": "int", "value": int(val)}
            else:
                entries[key] = {"type": "float", "value": val}
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
                entries[key] = {"type": "plist", "value": val}
        elif isinstance(val, bytes):
            continue

    if entries:
        return (domain, entries)
    return None


snapshot = {}
for domain, blacklist in sorted(domains_to_export.items()):
    result = export_domain(domain, blacklist)
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
                key, val = parts[0], parts[1]
                try:
                    pmset_snapshot[current_source][key] = int(val)
                except ValueError:
                    pmset_snapshot[current_source][key] = val
    break  # pmset -g custom shows both in one call

with open(PMSET_PATH, "w") as f:
    json.dump(pmset_snapshot, f, indent=2)
    f.write("\n")
