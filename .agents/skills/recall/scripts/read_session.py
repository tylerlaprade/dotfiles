#!/usr/bin/env python3
"""Pretty-print a Claude Code, Codex, or Grok session transcript."""

import json
import sys
from pathlib import Path

TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}

SKIP_MARKERS = (
    "<user_instructions>", "<environment_context>",
    "<permissions instructions>", "# AGENTS.md instructions",
    "<user_info>", "<system-reminder>", "<git_status>",
)


def extract_text(content):
    """Extract plain text from message content (string or array format)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type", "") in TEXT_BLOCK_TYPES
        ]
        return "\n".join(filter(None, parts))
    return ""


def iter_messages(path):
    """Yield (role, text) pairs from a session file, auto-detecting format."""
    fmt = detect_format(path)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip Codex state snapshots (legacy)
            if entry.get("record_type") == "state":
                continue

            if fmt == "claude":
                # Resolve role from type or role fields
                role = entry.get("role", "")
                if role not in ("user", "assistant"):
                    etype = entry.get("type", "")
                    if etype in ("user", "human"):
                        role = "user"
                    elif etype == "assistant":
                        role = "assistant"
                    else:
                        continue

                # Claude wraps in entry.message.content
                content = entry.get("message", {})
                if isinstance(content, dict):
                    content = content.get("content", "")
                elif not isinstance(content, str):
                    content = entry.get("content", "")

            elif fmt == "grok":
                if entry.get("synthetic_reason"):
                    continue
                etype = entry.get("type", "")
                if etype in ("user", "human"):
                    role = "user"
                elif etype == "assistant":
                    role = "assistant"
                else:
                    continue
                content = entry.get("content", "")

            else:
                # Codex — handle both legacy and current (wrapped payload) formats
                etype = entry.get("type", "")

                if etype in ("session_meta", "event_msg", "turn_context"):
                    continue

                if etype == "response_item":
                    payload = entry.get("payload", {})
                    role = payload.get("role", "")
                    content = payload.get("content", "")
                else:
                    role = entry.get("role", "")
                    content = entry.get("content", "")

                if role not in ("user", "assistant"):
                    continue

            text = extract_text(content)
            if not text or any(marker in text for marker in SKIP_MARKERS):
                continue

            yield role, text


def detect_format(path):
    """Detect whether a session file is Claude Code, Codex, or Grok format."""
    path_obj = Path(path)
    if path_obj.name == "chat_history.jsonl" or "/.grok/sessions/" in str(path_obj):
        return "grok"

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
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
    return "claude"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Pretty-print a Claude Code, Codex, or Grok session transcript")
    parser.add_argument("path", help="Path to a session .jsonl file")
    parser.add_argument("--pretty", action="store_true", help="Human-readable output instead of JSON")
    args = parser.parse_args()

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
