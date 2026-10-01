#!/usr/bin/python3
# Apple's stable python3 on purpose: macOS Automation/AppData permissions are
# keyed to the interpreter's binary path, and Homebrew uv/python move to a new
# Cellar path on every release, re-triggering the permission prompt each time.
"""Bidirectional sync of macOS defaults using per-domain files.

Each tracked domain gets its own JSON file under scripts/setup/macos-defaults/.
Direction is decided per key by a three-way merge against the state recorded
at the last run (see threeway.py). A domain written as host:<name> in the
config is per-host (defaults -currentHost).

Called by sync-dotfiles.sh (LaunchAgent: at login and daily).
  --adopt    fresh machine: write every repo value to the system, record that
             as the base, and export nothing (install.sh)
  --dry-run  print what would be written to the system and change nothing
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import plistlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from math import isclose
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, TypedDict, Union
from xml.parsers.expat import ExpatError

import threeway

if TYPE_CHECKING:
    from collections.abc import Callable

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SUFFIXED_UUID_RE = re.compile(r"^[A-Z]+-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(-\d+)?$")
ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HEX32PLUS_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
EPOCH_SECONDS_MIN = 1_000_000_000
EPOCH_SECONDS_MAX = 4_000_000_000
EPOCH_STRING_RE = re.compile(r"^[1-3]\d{9}(\.\d+)?$")  # epoch range 2001-2096, like the numeric check
SIZE_STRING_RE = re.compile(r"^\{-?\d+(\.\d+)?, -?\d+(\.\d+)?\}$")
RECT_STRING_RE = re.compile(r"^\{\{-?\d+(\.\d+)?, -?\d+(\.\d+)?\}, \{-?\d+(\.\d+)?, -?\d+(\.\d+)?\}\}$")
# Unanchored: catches embedded tokens too ("Bearer eyJ…", JSON-serialized auth blobs)
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ")
SECRET_KEY_RE = re.compile(r"token|secret|passw|credential|api[_-]?key", re.IGNORECASE)
ACCOUNT_RECORD_KEYS = {"AccountID", "AccountAlternateDSID", "AccountDescription", "AccountAuthenticationType"}

PlistValue = Union[bool, int, float, str, bytes, datetime, plistlib.UID, list["PlistValue"], dict[str, "PlistValue"]]
parse_plist: Callable[[bytes], PlistValue] = plistlib.loads


class BoolEntry(TypedDict):
    type: Literal["bool"]
    value: bool


class IntEntry(TypedDict):
    type: Literal["int"]
    value: int


class FloatEntry(TypedDict):
    type: Literal["float"]
    value: float


class StringEntry(TypedDict):
    type: Literal["string"]
    value: str


class PlistEntry(TypedDict):
    type: Literal["plist"]
    value: list[threeway.JSONValue] | dict[str, threeway.JSONValue]


class PlistFileEntry(TypedDict):
    type: Literal["plist-file"]
    file: str


class DatetimeEntry(TypedDict):
    type: Literal["datetime"]
    value: str


Entry = Union[BoolEntry, IntEntry, FloatEntry, StringEntry, PlistEntry, PlistFileEntry, DatetimeEntry]
Entries = dict[str, Entry]
NumberKind = Literal["int", "float"]


def unexpected(value: NoReturn) -> NoReturn:
    raise ValueError(f"unexpected value: {value!r}")


class Options(argparse.Namespace):
    adopt: bool = False
    dry_run: bool = False


SCRIPT_DIR = Path(__file__).absolute().parent

parser = argparse.ArgumentParser()
parser.add_argument("--adopt", action="store_true")
parser.add_argument("--dry-run", action="store_true")
options = parser.parse_args(namespace=Options())

REPO_ROOT = SCRIPT_DIR.parent.parent
SETUP_DIR = REPO_ROOT / "scripts" / "setup"
CONF_PATH = SETUP_DIR / "macos-defaults.conf"
DOMAIN_DIR = SETUP_DIR / "macos-defaults"

if not CONF_PATH.exists():
    sys.exit(0)

DOMAIN_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Config parsing (unchanged from before)
# ---------------------------------------------------------------------------

apple_whitelist: dict[str, list[str]] = {}
domain_blacklist: set[str] = set()
key_blacklists: dict[str, list[str]] = {}
global_key_blacklist: list[str] = []

with CONF_PATH.open() as f:
    for raw_line in f:
        line = raw_line.strip()
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
        elif domain_part in apple_whitelist:
            apple_whitelist[domain_part].extend(patterns)
        else:
            key_blacklists.setdefault(domain_part, []).extend(patterns)

# Build domain list
raw = subprocess.run(["defaults", "domains"], capture_output=True, text=True, check=False)
all_domains = [d.strip() for d in raw.stdout.split(",")]

domains_to_export: dict[str, list[str]] = {}
for domain in all_domains:
    if domain.startswith("com.apple."):
        if domain in apple_whitelist:
            domains_to_export[domain] = apple_whitelist[domain]
    elif domain not in domain_blacklist:
        domains_to_export[domain] = key_blacklists.get(domain, [])

for domain, patterns in apple_whitelist.items():
    if domain not in domains_to_export:
        domains_to_export[domain] = patterns

if "NSGlobalDomain" in apple_whitelist:
    domains_to_export["NSGlobalDomain"] = apple_whitelist["NSGlobalDomain"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def has_bytes(obj: PlistValue) -> bool:
    if isinstance(obj, bytes):
        return True
    if isinstance(obj, dict):
        return any(has_bytes(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_bytes(v) for v in obj)
    return False


def is_churn_scalar(val: PlistValue) -> bool:
    """Top-level scalar that's clearly state: timestamps (numeric or string), UUIDs,
    ISO datetimes, prefixed UUIDs, window-geometry strings (NSRect/NSSize forms)."""
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)) and EPOCH_SECONDS_MIN < val < EPOCH_SECONDS_MAX:
        return True
    if isinstance(val, str):
        if UUID_RE.match(val) or SUFFIXED_UUID_RE.match(val) or ISO_DATETIME_RE.match(val):
            return True
        if EPOCH_STRING_RE.match(val) or SIZE_STRING_RE.match(val) or RECT_STRING_RE.match(val):
            return True
    return False


def contains_email(val: PlistValue) -> bool:
    """Recursively scan for email-shaped strings (secret / account leak)."""
    if isinstance(val, str):
        return bool(EMAIL_RE.search(val))
    if isinstance(val, dict):
        return any(contains_email(v) for v in val.values())
    if isinstance(val, list):
        return any(contains_email(v) for v in val)
    return False


def contains_secret_string(val: PlistValue) -> bool:
    """Recursively scan for JWT-shaped strings (auth/session tokens)."""
    if isinstance(val, str):
        return bool(JWT_RE.search(val))
    if isinstance(val, dict):
        return any(contains_secret_string(v) for v in val.values())
    if isinstance(val, list):
        return any(contains_secret_string(v) for v in val)
    return False


def is_hex_keyed_dict(val: PlistValue) -> bool:
    """Dict whose keys are all 32+ hex chars — SHA-style digest keys (CloudKit caches)."""
    if not isinstance(val, dict) or not val:
        return False
    return all(HEX32PLUS_RE.match(k) for k in val)


def is_apple_account_record(val: PlistValue) -> bool:
    """Apple account dict (or list of them) — AccountID/DSID/etc. (MobileMeAccounts)."""
    if isinstance(val, list):
        return bool(val) and all(is_apple_account_record(x) for x in val)
    if isinstance(val, dict):
        return bool(set(val.keys()) & ACCOUNT_RECORD_KEYS)
    return False


def contains_file_bookmark(val: PlistValue, depth: int = 4) -> bool:
    """Recursively scan for {'book': bytes, ...} — NSURL file bookmarks (machine-specific paths)."""
    if depth < 0:
        return False
    if isinstance(val, dict):
        if any(k == "book" and isinstance(v, bytes) for k, v in val.items()):
            return True
        return any(contains_file_bookmark(v, depth - 1) for v in val.values())
    if isinstance(val, list):
        return any(contains_file_bookmark(v, depth - 1) for v in val)
    return False


def normalize_plist_value(obj: PlistValue) -> threeway.JSONValue:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: normalize_plist_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_plist_value(v) for v in obj]
    if isinstance(obj, (bytes, plistlib.UID)):
        raise TypeError(f"{obj!r} has no JSON form")
    return obj


def parse_scalar_entry(kind: threeway.JSONValue, value: threeway.JSONValue) -> Entry | None:
    if kind == "bool" and isinstance(value, bool):
        return {"type": "bool", "value": value}
    if kind == "int" and isinstance(value, int) and not isinstance(value, bool):
        return {"type": "int", "value": value}
    if kind == "float" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "float", "value": value}
    if kind == "string" and isinstance(value, str):
        return {"type": "string", "value": value}
    if kind == "datetime" and isinstance(value, str):
        return {"type": "datetime", "value": value}
    return None


def parse_entry(raw_entry: threeway.JSONValue) -> Entry:
    if not isinstance(raw_entry, dict):
        raise TypeError(f"defaults entry is not an object: {raw_entry!r}")
    kind = raw_entry.get("type")
    value = raw_entry.get("value")
    file = raw_entry.get("file")
    if kind == "plist" and isinstance(value, (list, dict)):
        return {"type": "plist", "value": value}
    if kind == "plist-file" and isinstance(file, str):
        return {"type": "plist-file", "file": file}
    entry = parse_scalar_entry(kind, value)
    if entry is None:
        raise ValueError(f"unrecognized defaults entry: {raw_entry!r}")
    return entry


def parse_entries(raw_entries: threeway.JSONObject) -> Entries:
    return {key: parse_entry(raw_entry) for key, raw_entry in raw_entries.items()}


def number_entry(kind: NumberKind, value: float) -> Entry:
    if kind == "int" and isinstance(value, int):
        return {"type": "int", "value": value}
    if kind == "float":
        return {"type": "float", "value": value}
    raise ValueError(f"int entry holds {value!r}")


def stabilize_number(key: str, kind: NumberKind, value: float, existing: Entries) -> Entry:
    """Preserve prior numeric typing/value when effectively identical."""
    entry = existing.get(key)
    if entry is None or (entry["type"] != "int" and entry["type"] != "float"):
        return number_entry(kind, value)

    old_value = round(entry["value"], 9) if entry["type"] == "float" and isinstance(entry["value"], float) else entry["value"]

    if entry["type"] == kind and old_value == value:
        return number_entry(entry["type"], old_value)

    if isclose(float(old_value), float(value), rel_tol=1e-9, abs_tol=1e-8):
        return number_entry(entry["type"], old_value)

    return number_entry(kind, value)


def stabilize_string(key: str, value: str, existing: Entries) -> str:
    """Preserve the prior serialization of JSON-in-string values when semantically
    identical — apps re-serialize embedded JSON with nondeterministic key order
    (e.g. AltTab's "exceptions"), which would otherwise churn the repo file."""
    entry = existing.get(key)
    if entry is None or entry["type"] != "string":
        return value
    old_value = entry["value"]
    if old_value == value:
        return value
    try:
        old_parsed = threeway.parse_json(old_value)
        new_parsed = threeway.parse_json(value)
    except ValueError:
        return value
    if isinstance(old_parsed, (dict, list)) and old_parsed == new_parsed:
        return old_value
    return value


def defaults_command(domain: str) -> tuple[list[str], str]:
    if domain.startswith("host:"):
        return ["defaults", "-currentHost"], domain[len("host:"):]
    return ["defaults"], domain


def read_system_domain(domain: str) -> dict[str, PlistValue] | None:
    command, name = defaults_command(domain)
    raw = subprocess.run([*command, "export", name, "-"], capture_output=True, check=False)
    if raw.returncode != 0:
        return None
    try:
        exported = parse_plist(raw.stdout)
    except (ValueError, ExpatError):
        return None
    return exported if isinstance(exported, dict) else None


def is_excluded(key: str, val: PlistValue, blacklist: list[str]) -> bool:
    if any(fnmatch.fnmatch(key, pat) for pat in blacklist):
        return True
    if SECRET_KEY_RE.search(key):
        return True
    return (
        is_churn_scalar(val)
        or contains_email(val)
        or contains_secret_string(val)
        or is_hex_keyed_dict(val)
        or is_apple_account_record(val)
        or contains_file_bookmark(val)
    )


def export_scalar(key: str, val: float | str, existing: Entries) -> Entry:
    if isinstance(val, bool):
        return {"type": "bool", "value": val}
    if isinstance(val, int):
        return stabilize_number(key, "int", val, existing)
    if isinstance(val, float):
        return stabilize_number(key, "float", round(val, 9), existing)
    return {"type": "string", "value": stabilize_string(key, val, existing)}


def export_container(domain: str, key: str, val: list[PlistValue] | dict[str, PlistValue]) -> Entry:
    if has_bytes(val):
        plist_dir = SETUP_DIR / "macos-plists"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist_name = f"{domain_file_name(domain)}.{key}.plist"
        with (plist_dir / plist_name).open("wb") as pf:
            plistlib.dump({key: val}, pf, fmt=plistlib.FMT_XML)
        return {"type": "plist-file", "file": f"macos-plists/{plist_name}"}
    normalized = normalize_plist_value(val)
    if not isinstance(normalized, (list, dict)):
        raise TypeError(f"{key} normalized to {normalized!r}")
    return {"type": "plist", "value": normalized}


def export_entry(domain: str, key: str, val: PlistValue, existing: Entries) -> Entry | None:
    if isinstance(val, (bool, int, float, str)):
        return export_scalar(key, val, existing)
    if isinstance(val, (list, dict)):
        return export_container(domain, key, val)
    if isinstance(val, (bytes, plistlib.UID)):
        return None
    return {"type": "datetime", "value": val.isoformat()}


def export_domain(domain: str, blacklist: list[str], existing: Entries) -> Entries | None:
    """Export a single domain from system, returning entries dict or None."""
    exported = read_system_domain(domain)
    if exported is None:
        return None

    entries: Entries = {}
    for key in sorted(exported):
        val = exported[key]
        if is_excluded(key, val, blacklist):
            continue
        entry = export_entry(domain, key, val, existing)
        if entry is not None:
            entries[key] = entry

    return entries or None


def run_defaults(args: list[str]) -> bool:
    """Run a defaults command. No sudo retry: defaults run as root writes to
    root's preference domain, not the user's."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return result.returncode == 0


def import_plist_value(command: list[str], name: str, key: str, value: threeway.JSONValue) -> None:
    with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
        plistlib.dump({key: value}, tmp, fmt=plistlib.FMT_XML)
        tmp_path = Path(tmp.name)
    run_defaults([*command, "import", name, str(tmp_path)])
    tmp_path.unlink()


def apply_entry(command: list[str], name: str, key: str, entry: Entry) -> None:
    if entry["type"] == "plist-file":
        run_defaults([*command, "import", name, str(SETUP_DIR / entry["file"])])
    elif entry["type"] == "bool":
        run_defaults([*command, "write", name, key, "-bool", str(entry["value"]).lower()])
    elif entry["type"] == "int":
        run_defaults([*command, "write", name, key, "-int", str(entry["value"])])
    elif entry["type"] == "float":
        run_defaults([*command, "write", name, key, "-float", str(entry["value"])])
    elif entry["type"] == "string":
        run_defaults([*command, "write", name, key, "-string", entry["value"]])
    elif entry["type"] == "plist":
        import_plist_value(command, name, key, entry["value"])
    elif entry["type"] == "datetime":
        return
    else:
        unexpected(entry)


def apply_domain(domain: str, entries: Entries) -> None:
    """Write entries to system via `defaults write`."""
    command, name = defaults_command(domain)
    for key, entry in entries.items():
        apply_entry(command, name, key, entry)


def domain_file_name(domain: str) -> str:
    # Slash-containing domains (com.apple.LaunchServices/...) must not create subdirs
    return domain.replace("/", "--").replace("host:", "ByHost.")


def domain_path(domain: str) -> Path:
    return DOMAIN_DIR / f"{domain_file_name(domain)}.json"


def read_domain_file(domain: str) -> Entries | None:
    path = domain_path(domain)
    raw_entries = threeway.read_json_object(path)
    if raw_entries is None:
        if path.exists():
            raise ValueError(f"{path}: expected a JSON object")
        return None
    return parse_entries(raw_entries)


def write_domain_file(domain: str, entries: Entries) -> None:
    threeway.write_json(domain_path(domain), entries)


# ---------------------------------------------------------------------------
# Bidirectional sync — per key, three-way against the last reconciled state
# ---------------------------------------------------------------------------

applied_domains: set[str] = set()


def base_name(domain: str) -> str:
    return f"macos-defaults/{domain_file_name(domain)}"


def load_domain_base(domain: str) -> Entries | None:
    base = threeway.load_base(base_name(domain))
    if base is None:
        return None
    try:
        return parse_entries(base)
    except (TypeError, ValueError):
        return None


def displayed_value(entry: Entry) -> object:
    if entry["type"] == "plist-file":
        return entry
    return entry["value"]


def write_to_system(domain: str, updates: Entries) -> None:
    if not updates:
        return
    if options.dry_run:
        for key, entry in updates.items():
            print(f"would write {domain} {key} = {json.dumps(displayed_value(entry), sort_keys=True)}")
        return
    apply_domain(domain, updates)
    applied_domains.add(domain)
    threeway.log_applied("macos-defaults", domain, updates)


for domain in sorted(domains_to_export):
    blacklist = global_key_blacklist + domains_to_export[domain]
    repo_entries = read_domain_file(domain)

    if options.adopt:
        if repo_entries is not None:
            write_to_system(domain, repo_entries)
            if not options.dry_run:
                threeway.save_base(base_name(domain), repo_entries)
        continue

    local_entries = export_domain(domain, blacklist, repo_entries or {})
    if local_entries is None and repo_entries is None:
        continue

    if repo_entries is None:
        # New domain, no repo file yet
        if not options.dry_run and local_entries is not None:
            write_domain_file(domain, local_entries)
            threeway.save_base(base_name(domain), local_entries)
        continue
    if local_entries is None:
        # Domain in repo but app not installed (or export failed) — keep the
        # file and the base, so a repo change still lands once the domain appears.
        continue

    base = load_domain_base(domain)
    if base is None:
        merged = threeway.merge_unbased(local_entries, repo_entries)
    else:
        merged = threeway.merge(base, local_entries, repo_entries, repo_can_delete=False)
    updates, _ = threeway.changes(local_entries, merged)
    write_to_system(domain, updates)
    if options.dry_run:
        continue
    if updates:
        # Re-export to capture what the system actually accepted
        merged = export_domain(domain, blacklist, merged) or merged
    if merged != repo_entries:
        write_domain_file(domain, merged)
    threeway.save_base(base_name(domain), merged)

# Restart affected services if we applied anything
if applied_domains and not options.dry_run:
    needs_restart: set[str] = set()
    for domain in applied_domains:
        if "dock" in domain.lower():
            needs_restart.add("Dock")
        if "finder" in domain.lower():
            needs_restart.add("Finder")
        if "systemuiserver" in domain.lower():
            needs_restart.add("SystemUIServer")
    for proc in needs_restart:
        subprocess.run(["killall", proc], stderr=subprocess.DEVNULL, check=False)

# ---------------------------------------------------------------------------
# Login items — three-way merge, same rule as the defaults domains.
# An empty or failed osascript read leaves the repo file untouched.
# ---------------------------------------------------------------------------

# Apps that register their own login item (SMAppService). A SharedFileList
# entry beside that registration opens the app twice.
LOGIN_ITEMS_SKIP = {"Monologue"}

LOGIN_ITEMS_READ = """
set output to ""
tell application "System Events"
    repeat with li in login items
        set itemPath to path of li
        if itemPath is missing value then set itemPath to ""
        set output to output & (name of li) & linefeed & itemPath & linefeed
    end repeat
end tell
return output
"""

LOGIN_ITEMS_PATH = SETUP_DIR / "login-items.json"
LOGIN_ITEMS_BASE = "login-items"


def applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_osascript(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)


def read_login_items_file() -> dict[str, str]:
    if not LOGIN_ITEMS_PATH.exists():
        return {}
    with LOGIN_ITEMS_PATH.open() as f:
        items = threeway.load_json(f)
    if not isinstance(items, list):
        raise TypeError(f"{LOGIN_ITEMS_PATH}: expected a JSON list")
    tracked: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise TypeError(f"{LOGIN_ITEMS_PATH}: expected objects, found {item!r}")
        name = item.get("name")
        path = item.get("path")
        if name in LOGIN_ITEMS_SKIP or not path:
            continue
        if not isinstance(name, str) or not isinstance(path, str):
            raise TypeError(f"{LOGIN_ITEMS_PATH}: expected a name and path, found {item!r}")
        tracked[path] = name
    return tracked


def read_live_login_items() -> tuple[dict[str, str], list[str]] | None:
    """(tracked path→name, skipped names present on this Mac). None if unread."""
    raw = run_osascript(LOGIN_ITEMS_READ)
    if raw.returncode != 0:
        return None
    if not raw.stdout.strip():
        return {}, []
    lines = raw.stdout.rstrip("\n").split("\n")
    tracked: dict[str, str] = {}
    skipped: list[str] = []
    for name, path in zip(lines[0::2], lines[1::2]):
        if not name or not path:
            continue
        if name in LOGIN_ITEMS_SKIP or Path(path).name == "Monologue.app":
            skipped.append(name)
            continue
        tracked[path] = name
    return tracked, skipped


def is_installed(path: str) -> bool:
    return Path(path).expanduser().exists()


def keep_uninstalled(local_map: dict[str, str], repo_map: dict[str, str], base: dict[str, str] | None) -> dict[str, str]:
    """An app that is not on this Mac is not a local deletion."""
    local = dict(local_map)
    candidates = dict(repo_map)
    if base:
        candidates.update(base)
    for path, name in candidates.items():
        if path not in local and not is_installed(path):
            local[path] = name
    return local


def write_login_items_file(merged: dict[str, str]) -> None:
    items = [
        {"name": name, "path": path}
        for path, name in sorted(merged.items(), key=lambda item: (item[1].lower(), item[0]))
    ]
    threeway.write_json(LOGIN_ITEMS_PATH, items)


def add_login_item(path: str) -> subprocess.CompletedProcess[str]:
    escaped = applescript_string(path)
    script = (
        'tell application "System Events" to make login item at end '
        f'with properties {{path:"{escaped}", hidden:false}}'
    )
    return run_osascript(script)


def delete_login_item(name: str) -> subprocess.CompletedProcess[str]:
    escaped = applescript_string(name)
    script = f"""
tell application "System Events"
    repeat with li in (every login item whose name is "{escaped}")
        delete li
    end repeat
end tell
"""
    return run_osascript(script)


def preview_login_items(local_map: dict[str, str], merged: dict[str, str], skipped: list[str]) -> None:
    for name in skipped:
        print(f"would remove login item {name}")
    for path in sorted(set(local_map) - set(merged)):
        print(f"would remove login item {local_map[path]}")
    for path in sorted(set(merged) - set(local_map)):
        print(f"would add login item {merged[path]}")


def apply_login_items(local_map: dict[str, str], merged: dict[str, str], skipped: list[str]) -> None:
    for name in skipped:
        delete_login_item(name)
    for path in sorted(set(local_map) - set(merged)):
        delete_login_item(local_map[path])
    for path, name in sorted(merged.items()):
        if path in local_map or not is_installed(path):
            continue
        result = add_login_item(path)
        if result.returncode != 0:
            print(f"login item {name}: {result.stderr.strip()}", file=sys.stderr)


def sync_login_items() -> None:
    live = read_live_login_items()
    if live is None:
        print("login items: could not read the live list; leaving the repo file alone", file=sys.stderr)
        return
    local_map, skipped = live
    repo_map = read_login_items_file()
    recorded_base = None if options.adopt else threeway.load_base(LOGIN_ITEMS_BASE)
    base = None if recorded_base is None else threeway.strings(recorded_base)
    comparable = keep_uninstalled(local_map, repo_map, base)
    if base is None:
        merged = threeway.merge_unbased(comparable, repo_map)
    else:
        merged = threeway.merge(base, comparable, repo_map)

    if options.dry_run:
        preview_login_items(local_map, merged, skipped)
        return

    apply_login_items(local_map, merged, skipped)

    if merged != repo_map:
        write_login_items_file(merged)
    threeway.save_base(LOGIN_ITEMS_BASE, merged)


sync_login_items()
