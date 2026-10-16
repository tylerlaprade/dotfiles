#!/opt/homebrew/bin/python3
"""Report every live Claude Code session's health: status, what it waits on, how its last turn ended, and work left in flight."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

type JSON = dict[str, object]

parse_json: Callable[[str], object] = json.loads

HOME = Path.home()
TAIL_BYTES = 400_000
RECENT_MESSAGES = 6
SUBAGENT_WINDOW_HOURS = 6


def read_json(path: Path) -> JSON | None:
    try:
        value = parse_json(path.read_text())
    except OSError, ValueError:
        return None
    return cast("JSON", value) if isinstance(value, dict) else None


def alive(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def tail_rows(path: Path, size: int = TAIL_BYTES) -> list[JSON]:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - size))
        lines = handle.read().decode("utf-8", "replace").splitlines()[1:]
    rows: list[JSON] = []
    for line in lines:
        try:
            value = parse_json(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(cast("JSON", value))
    return rows


def content_text(row: JSON) -> str:
    message = row.get("message")
    content = (
        cast("JSON", message).get("content") if isinstance(message, dict) else None
    )
    return content if isinstance(content, str) else json.dumps(content)


def turn_problems(rows: list[JSON]) -> list[str]:
    turns = [
        row
        for row in rows
        if row.get("type") in {"user", "assistant"} and not row.get("isSidechain")
    ]
    problems: set[str] = set()
    for row in turns[-RECENT_MESSAGES:]:
        text = content_text(row)
        if row.get("isApiErrorMessage") or "API Error" in text[:300]:
            problems.add("went to sleep" if "went to sleep" in text else "api error")
        if "Tool call interrupted" in text or "Request interrupted" in text:
            problems.add("interrupted call")
    return sorted(problems)


def subagents(transcript: Path, now: float) -> list[str]:
    folder = transcript.with_suffix("") / "subagents"
    found: list[str] = []
    for path in sorted(
        folder.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True
    ):
        age_minutes = (now - path.stat().st_mtime) / 60
        if age_minutes > SUBAGENT_WINDOW_HOURS * 60:
            break
        turns = [
            row
            for row in tail_rows(path, 200_000)
            if row.get("type") in {"user", "assistant"}
        ]
        last = turns[-1] if turns else {}
        message = last.get("message")
        stop = (
            cast("JSON", message).get("stop_reason")
            if isinstance(message, dict)
            else None
        )
        ending = (
            "finished" if stop == "end_turn" else f"last {last.get('type', '?')} entry"
        )
        found.append(
            f"{path.stem.removeprefix('agent-')[:28]} ({age_minutes:.0f}m ago, {ending})"
        )
    return found


def background_tasks() -> dict[str, list[str]]:
    store = HOME / ".config/session-guard/active-sessions.json"
    try:
        records = parse_json(store.read_text())
    except OSError, ValueError:
        return {}
    tasks: dict[str, list[str]] = {}
    for record in cast("list[JSON]", records) if isinstance(records, list) else []:
        listed = record.get("background_tasks")
        if isinstance(listed, list) and listed:
            names = [
                f"{task.get('description') or task.get('id')} ({task.get('type') or '?'})"
                for task in cast("list[JSON]", listed)
            ]
            tasks[str(record.get("session_id"))] = names
    return tasks


def sweep() -> list[JSON]:
    now = time.time()
    in_flight = background_tasks()
    report: list[JSON] = []
    for path in sorted((HOME / ".claude/sessions").glob("*.json")):
        native = read_json(path)
        if native is None or not alive(native.get("pid")):
            continue
        session_id = str(native.get("sessionId"))
        transcripts = list((HOME / ".claude/projects").glob(f"*/{session_id}.jsonl"))
        rows = tail_rows(transcripts[0]) if transcripts else []
        stamps = [str(row.get("timestamp")) for row in rows if row.get("timestamp")]
        updated = native.get("statusUpdatedAt")
        report.append(
            {
                "name": native.get("name") or "-",
                "session": session_id,
                "cwd": native.get("cwd"),
                "status": native.get("status"),
                "waiting_for": native.get("waitingFor"),
                "status_minutes": round((now - updated / 1000) / 60)
                if isinstance(updated, (int, float))
                else None,
                "last_entry": stamps[-1] if stamps else None,
                "problems": turn_problems(rows),
                "subagents": subagents(transcripts[0], now) if transcripts else [],
                "background_tasks": in_flight.get(session_id, []),
            }
        )
    return report


class Args(argparse.Namespace):
    json: bool = False
    all: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--all", action="store_true", help="include sessions with nothing to report"
    )
    args = parser.parse_args(namespace=Args())
    report = sweep()
    if args.json:
        print(json.dumps(report, indent=1))
        return 0
    for entry in report:
        notable = (
            entry["problems"]
            or entry["subagents"]
            or entry["background_tasks"]
            or entry["status"] != "idle"
        )
        if not (args.all or notable):
            continue
        waiting = f" waiting for {entry['waiting_for']}" if entry["waiting_for"] else ""
        print(
            f"{entry['name']} [{str(entry['session'])[:8]}] {entry['status']}{waiting}, "
            f"status {entry['status_minutes']}m old, last entry {entry['last_entry']}"
        )
        for label in ("problems", "subagents", "background_tasks"):
            items = cast("list[str]", entry[label])
            if items:
                print(f"  {label.replace('_', ' ')}: {'; '.join(items)}")
    quiet = sum(
        1
        for entry in report
        if not (
            entry["problems"]
            or entry["subagents"]
            or entry["background_tasks"]
            or entry["status"] != "idle"
        )
    )
    print(
        f"{len(report)} live sessions; {quiet} idle with nothing in flight{'' if args.all else ' (hidden; --all shows them)'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
