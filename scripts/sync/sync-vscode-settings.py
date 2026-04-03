#!/usr/bin/env -S uv run --script
"""Bidirectional sync of VS Code settings with local-only secrets.

Keeps non-secret top-level settings in sync between the repo file and the live
VS Code settings file using last-writer-wins semantics. Secret keys remain in a
separate local secrets file and are rehydrated into the live settings file.

Comments in the repo file (JSONC) are preserved across round-trips.
"""

import json
import os
import re
import sys

repo_path, local_path, secrets_path = sys.argv[1], sys.argv[2], sys.argv[3]
SECRET_KEYS = {"sourcery.token"}

_JSONC_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'
    r"|//[^\n]*"
    r"|/\*[\s\S]*?\*/",
)


def _strip_comments(text):
    return _JSONC_RE.sub(lambda m: m.group() if m.group().startswith('"') else "", text)


def _find_value_end(text, pos):
    """Given text and a position just after a colon, find the end of the JSON value."""
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    if pos >= len(text):
        return pos
    ch = text[pos]
    if ch == '"':
        i = pos + 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
            elif text[i] == '"':
                return i + 1
            else:
                i += 1
        return i
    if ch in "{[":
        close = "}" if ch == "{" else "]"
        depth, i, in_str = 1, pos + 1, False
        while i < len(text) and depth > 0:
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == ch:
                depth += 1
            elif c == close:
                depth -= 1
            i += 1
        return i
    # number, boolean, null
    i = pos
    while i < len(text) and text[i] not in ",}\n\r]":
        i += 1
    return i


def _get_indent(text, pos):
    """Get the whitespace indentation of the line containing pos."""
    line_start = text.rfind("\n", 0, pos) + 1
    indent = ""
    for c in text[line_start:]:
        if c in " \t":
            indent += c
        else:
            break
    return indent


def patch_jsonc(raw, old_dict, new_dict):
    """Patch a JSONC string: update/add/remove top-level keys, preserving comments."""
    text = raw

    # Update changed values (iterate in reverse position order to preserve offsets)
    changes = []
    for key in new_dict:
        if key in old_dict and old_dict[key] != new_dict[key]:
            key_str = json.dumps(key)
            key_pos = text.find(key_str)
            if key_pos == -1:
                continue
            colon = text.index(":", key_pos + len(key_str))
            val_start = colon + 1
            # Skip whitespace to get to actual value start
            ws_end = val_start
            while ws_end < len(text) and text[ws_end] in " \t":
                ws_end += 1
            val_end = _find_value_end(text, val_start)
            indent = _get_indent(text, key_pos)
            new_val = json.dumps(new_dict[key], indent=2).replace("\n", "\n" + indent)
            changes.append((val_start, val_end, " " + new_val))

    for start, end, replacement in sorted(changes, reverse=True):
        text = text[:start] + replacement + text[end:]

    # Remove deleted keys
    for key in old_dict:
        if key not in new_dict:
            key_str = json.dumps(key)
            key_pos = text.find(key_str)
            if key_pos == -1:
                continue
            line_start = text.rfind("\n", 0, key_pos)
            if line_start == -1:
                line_start = 0
            colon = text.index(":", key_pos + len(key_str))
            val_end = _find_value_end(text, colon + 1)
            end = val_end
            while end < len(text) and text[end] in " \t":
                end += 1
            if end < len(text) and text[end] == ",":
                end += 1
            while end < len(text) and text[end] in " \t":
                end += 1
            # Consume trailing line comment
            if end < len(text) and text[end : end + 2] == "//":
                while end < len(text) and text[end] != "\n":
                    end += 1
            if end < len(text) and text[end] == "\n":
                end += 1
            text = text[: line_start + (1 if line_start > 0 else 0)] + text[end:]

    # Add new keys
    new_keys = [k for k in new_dict if k not in old_dict]
    if new_keys:
        last_brace = text.rfind("}")
        before = text[:last_brace].rstrip()
        if before and before[-1] not in (",", "{"):
            before += ","
        additions = []
        for key in new_keys:
            val = json.dumps(new_dict[key], indent=2).replace("\n", "\n\t")
            additions.append(f"\t{json.dumps(key)}: {val}")
        text = before + "\n" + ",\n".join(additions) + "\n}\n"

    return text


def load_jsonc(path):
    if not os.path.exists(path):
        return {}, None
    with open(path) as f:
        raw = f.read()
    return json.loads(_strip_comments(raw)), raw


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.loads(_strip_comments(f.read()))


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


repo_settings, repo_raw = load_jsonc(repo_path)
local_settings = load_json(local_path)
secret_settings = load_json(secrets_path)

local_visible = {k: v for k, v in local_settings.items() if k not in SECRET_KEYS}
local_secrets = {k: v for k, v in local_settings.items() if k in SECRET_KEYS}

repo_mtime = os.path.getmtime(repo_path) if os.path.exists(repo_path) else 0
local_mtime = os.path.getmtime(local_path) if os.path.exists(local_path) else 0

merged_visible = local_visible if local_mtime > repo_mtime else repo_settings
merged_secrets = local_secrets or secret_settings

if repo_settings != merged_visible:
    if repo_raw is not None:
        patched = patch_jsonc(repo_raw, repo_settings, merged_visible)
        with open(repo_path, "w") as f:
            f.write(patched)
    else:
        write_json(repo_path, merged_visible)

if merged_secrets:
    if secret_settings != merged_secrets:
        write_json(secrets_path, merged_secrets)
elif os.path.exists(secrets_path):
    os.remove(secrets_path)

merged_local = {**merged_visible, **merged_secrets}
if local_settings != merged_local:
    write_json(local_path, merged_local)
