#!/usr/bin/env -S uv run --script
"""Format settings.json to match Claude Code's native JSON serializer.

Claude Code uses Node.js JSON.stringify(obj, null, 2) — standard 2-space
indented JSON with insertion-order keys. Running this after any edit normalizes
formatting so TUI setting toggles don't create spurious diffs.
"""
import json
import sys

path = sys.argv[1]
with open(path) as f:
    data = json.load(f)

text = json.dumps(data, indent=2)
text += "\n"

with open(path, "w") as f:
    f.write(text)
