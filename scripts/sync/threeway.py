"""Three-way merge for the bidirectional syncs.

The base is the state both sides agreed on at the last run. A side that moved
away from the base carries a real change; when both moved, the live machine
wins, because a change made here was made on purpose. Modification times play
no part, so a git checkout, stash, or rebase cannot make the repo look newer
than it is.

merge() with no base returns the live side. That is not a fresh machine:
callers use merge_unbased(), which adopts a repo that already has content
and does not export the live machine over it. An empty repo still takes the
live side, so the first export from the original machine works.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, Union

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from typing import TextIO

JSONValue = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]
JSONObject = dict[str, JSONValue]
Value = TypeVar("Value")

load_json: Callable[[TextIO], JSONValue] = json.load
parse_json: Callable[[str], JSONValue] = json.loads

BASE_DIR = Path("~/.local/state/dotfiles-sync").expanduser()
LOG_PATH = Path("~/Library/Logs/dotfiles-sync.log").expanduser()


def read_json_object(path: Path) -> JSONObject | None:
    if not path.exists():
        return None
    with path.open() as f:
        loaded = load_json(f)
    return loaded if isinstance(loaded, dict) else None


def write_json(path: Path, value: object, *, sort_keys: bool = False) -> None:
    with path.open("w") as f:
        json.dump(value, f, indent=2, sort_keys=sort_keys)
        f.write("\n")


def strings(values: Mapping[str, JSONValue]) -> dict[str, str]:
    return {key: value for key, value in values.items() if isinstance(value, str)}


def base_path(name: str) -> Path:
    return BASE_DIR / f"{name}.json"


def load_base(name: str) -> JSONObject | None:
    try:
        return read_json_object(base_path(name))
    except ValueError:
        return None


def save_base(name: str, value: Mapping[str, object]) -> None:
    path = base_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value, sort_keys=True)


def same(first: Mapping[str, Value], second: Mapping[str, Value], key: str) -> bool:
    if key not in first or key not in second:
        return (key in first) == (key in second)
    return first[key] == second[key]


def merge_unbased(local: Mapping[str, Value], repo: Mapping[str, Value]) -> dict[str, Value]:
    """No recorded base. Adopt the shared repo when it has content."""
    if repo:
        return dict(repo)
    return dict(local)


def merge(
    base: Mapping[str, Value] | None,
    local: Mapping[str, Value],
    repo: Mapping[str, Value],
    *,
    repo_can_delete: bool = True,
) -> dict[str, Value]:
    if base is None:
        return dict(local)
    merged: dict[str, Value] = {}
    for key in sorted(set(base) | set(local) | set(repo)):
        repo_moved = not same(local, repo, key) and same(local, base, key)
        source = repo if repo_moved and (key in repo or repo_can_delete) else local
        if key in source:
            merged[key] = source[key]
    return merged


def changes(local: Mapping[str, Value], merged: Mapping[str, Value]) -> tuple[dict[str, Value], list[str]]:
    """What the live side must take from the merge: (updates, removals)."""
    updates = {key: value for key, value in merged.items() if key not in local or local[key] != value}
    removals = [key for key in local if key not in merged]
    return updates, removals


def log_applied(sync: str, target: str, updates: Mapping[str, object], removals: Iterable[str] = ()) -> None:
    removed = list(removals)
    if not updates and not removed:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOG_PATH.open("a") as f:
        for key, value in updates.items():
            f.write(f"{stamp} {sync} {target} {key} = {json.dumps(value, sort_keys=True)}\n")
        for key in removed:
            f.write(f"{stamp} {sync} {target} {key} removed\n")
