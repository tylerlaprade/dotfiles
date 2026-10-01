#!/opt/homebrew/bin/python3
"""Pretty-print a Claude Code, Codex, Grok, Antigravity, or OpenCode session transcript."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

# Share the indexer's idea of what counts as text and what is harness noise,
# so this prints exactly the messages /recall can return.
from recall import (
    CODEX_SKIP_MARKERS,
    GROK_SKIP_MARKERS,
    OPENCODE_PATH_SEP,
    JSONObject,
    JSONValue,
    Message,
    antigravity_message,
    claude_turn,
    codex_turn,
    grok_turn,
    opencode_messages,
    parse_json,
    split_opencode_path,
    turn_message,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

SKIP_MARKERS: dict[str, tuple[str, ...]] = {
    "claude": (),
    "codex": CODEX_SKIP_MARKERS,
    "grok": GROK_SKIP_MARKERS,
    "antigravity": (),
}

__all__ = ["SKIP_MARKERS", "detect_format", "iter_messages", "turn_message"]


def iter_json_objects(path: str) -> Iterator[JSONObject]:
    """Every JSON object line of a transcript, skipping blank and unparseable ones."""
    with Path(path).open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = parse_json(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


def codex_entry_turn(entry: JSONObject) -> tuple[JSONValue, JSONValue] | None:
    """The role and content of a Codex entry, or None for session bookkeeping."""
    if entry.get("type", "") in ("session_meta", "event_msg", "turn_context"):
        return None
    return codex_turn(entry)


TURN_READERS: dict[str, Callable[[JSONObject], tuple[JSONValue, JSONValue] | None]] = {
    "claude": claude_turn,
    "codex": codex_entry_turn,
    "grok": grok_turn,
}


def entry_message(fmt: str, entry: JSONObject) -> Message | None:
    """The (role, text) one transcript entry contributes, or None."""
    if fmt == "antigravity":
        return antigravity_message(entry)
    turn = TURN_READERS[fmt](entry)
    return turn_message(*turn, SKIP_MARKERS[fmt]) if turn else None


def iter_messages(path: str) -> Iterator[Message]:
    """Yield (role, text) pairs from a session, auto-detecting format."""
    if OPENCODE_PATH_SEP in path:
        db_path, session_id = split_opencode_path(path)
        yield from opencode_messages(db_path, session_id)
        return

    fmt = detect_format(path)
    for entry in iter_json_objects(path):
        # Skip Codex state snapshots (legacy)
        if entry.get("record_type") == "state":
            continue
        message = entry_message(fmt, entry)
        if message:
            yield message


def detect_format_from_entry(entry: JSONObject) -> str | None:
    """The format one entry gives away, or None when it could be any of them."""
    if entry.get("record_type") == "state":
        return "codex"
    if "parentUuid" in entry or "message" in entry:
        return "claude"
    if "id" in entry and "instructions" in entry:
        return "codex"
    # Current Codex format uses type: "session_meta"
    if entry.get("type") == "session_meta":
        return "codex"
    # Grok: top-level type user/assistant/system/reasoning/tool_result
    # with synthetic_reason, or content blocks without message wrapper.
    if entry.get("type") in ("reasoning", "tool_result") or "synthetic_reason" in entry:
        return "grok"
    return None


def detect_format(path: str) -> str:
    """Detect whether a session file is Claude, Codex, Grok, or Antigravity."""
    path_obj = Path(path)
    if path_obj.name == "chat_history.jsonl" or "/.grok/sessions/" in str(path_obj):
        return "grok"
    if path_obj.name == "transcript.jsonl" or "/antigravity-cli/" in str(path_obj):
        return "antigravity"
    for entry in iter_json_objects(path):
        fmt = detect_format_from_entry(entry)
        if fmt:
            return fmt
    return "claude"


class Arguments(argparse.Namespace):
    path: str
    pretty: bool


def main() -> None:
    parser = argparse.ArgumentParser(description="Pretty-print a Claude Code, Codex, Grok, Antigravity, or OpenCode session transcript")
    parser.add_argument("path", help="Path to a session .jsonl file, or <opencode.db>#<session id>")
    parser.add_argument("--pretty", action="store_true", help="Human-readable output instead of JSON")
    args = parser.parse_args(namespace=Arguments())

    if args.pretty:
        for role, text in iter_messages(args.path):
            print(f"--- {role} ---")
            print(text[:500])
            print()
    else:
        msgs = [{"role": role, "text": text} for role, text in iter_messages(args.path)]
        print(json.dumps(msgs, indent=2))


if __name__ == "__main__":
    main()
