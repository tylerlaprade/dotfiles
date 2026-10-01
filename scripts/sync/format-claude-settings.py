#!/usr/bin/env -S uv run --script
"""Format settings.json to match Claude Code's native JSON serializer.

Claude Code uses Node.js JSON.stringify(obj, null, 2) — standard 2-space
indented JSON with insertion-order keys. Running this after any edit normalizes
formatting so TUI setting toggles don't create spurious diffs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from collections.abc import Callable

JSONValue = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]
parse_json: Callable[[str], JSONValue] = json.loads


def read_settings(path: Path) -> JSONValue:
    return parse_json(path.read_text())


path = Path(sys.argv[1])
data = read_settings(path)

text = json.dumps(data, indent=2)
text += "\n"

path.write_text(text)
