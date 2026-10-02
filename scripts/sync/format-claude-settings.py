#!/opt/homebrew/bin/python3
"""Format settings.json to match Claude Code's native JSON serializer.

Claude Code uses Node.js JSON.stringify(obj, null, 2) — standard 2-space
indented JSON with insertion-order keys. Running this after any edit normalizes
formatting so TUI setting toggles don't create spurious diffs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None
# json.loads is typed to return Any; this is the one boundary that declares its result plain JSON.
parse_json: Callable[[str], JSONValue] = json.loads


def read_settings(path: Path) -> JSONValue:
    return parse_json(path.read_text())


path = Path(sys.argv[1])
data = read_settings(path)

text = json.dumps(data, indent=2)
text += "\n"

path.write_text(text)
