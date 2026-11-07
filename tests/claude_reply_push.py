# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast, final, override

HOOK = Path(__file__).resolve().parents[1] / "scripts/bin/claude-reply-push.sh"


@final
class ClaudeReplyPushTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.transcript = Path(directory.name) / "session.jsonl"

    def write_turns(self, *origins: str) -> None:
        lines: list[object] = [
            {"type": "user", "turnOrigin": origin, "message": {"content": "hi"}}
            for origin in origins
        ]
        quoting_a_marker = 'Tyler wrote "turnOrigin":"human" here'
        lines.append(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": quoting_a_marker}]},
            }
        )
        self.transcript.write_text(
            "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines)
        )

    def run_hook(self, *, active: bool = False) -> str:
        payload = json.dumps(
            {"stop_hook_active": active, "transcript_path": str(self.transcript)}
        )
        result = subprocess.run(
            [str(HOOK)], input=payload, capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_a_turn_tyler_started_asks_for_a_push(self) -> None:
        self.write_turns("peer", "human")
        decision = cast("dict[str, str]", json.loads(self.run_hook()))
        self.assertEqual(decision["decision"], "block")
        self.assertIn("PushNotification", decision["reason"])

    def test_turns_others_started_push_nothing(self) -> None:
        for origin in ("peer", "task_notification", "scheduled"):
            with self.subTest(origin=origin):
                self.write_turns("human", origin)
                self.assertEqual(self.run_hook(), "")

    def test_a_continued_turn_is_never_asked_twice(self) -> None:
        self.write_turns("human")
        self.assertEqual(self.run_hook(active=True), "")

    def test_a_missing_transcript_pushes_nothing(self) -> None:
        self.transcript = self.transcript.with_name("absent.jsonl")
        self.assertEqual(self.run_hook(), "")


if __name__ == "__main__":
    unittest.main()
