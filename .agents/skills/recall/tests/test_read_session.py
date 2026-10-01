"""Tests for read_session.py.

Its job is to print the transcript behind a search result, so it has to agree
with the indexer about which messages exist. When the two drifted, /recall
could return an excerpt from a message read_session refused to show.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import read_session
import recall

if TYPE_CHECKING:
    from recall import JSONObject


class SharedWithTheIndexer(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp: Path = Path(tmp.name)

    def write(self, name: str, entries: list[JSONObject]) -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        return path

    def test_one_definition_of_message_text(self) -> None:
        self.assertIs(read_session.turn_message, recall.turn_message)

    def test_markers_are_applied_per_source_as_the_indexer_does(self) -> None:
        self.assertEqual(read_session.SKIP_MARKERS["codex"], recall.CODEX_SKIP_MARKERS)
        self.assertEqual(read_session.SKIP_MARKERS["grok"], recall.GROK_SKIP_MARKERS)
        self.assertEqual(read_session.SKIP_MARKERS["claude"], ())

    def test_claude_prints_what_the_indexer_stored(self) -> None:
        """A Claude turn carrying a system-reminder is indexed whole, so it has
        to be printable. Skipping it here lost the user's own words with it."""
        entries: list[JSONObject] = [
            {"type": "user", "cwd": "/w",
             "message": {"content": "the real question<system-reminder>noise</system-reminder>"}},
            {"type": "assistant", "message": {"content": "the answer"}},
        ]
        path = self.write("proj/1111.jsonl", entries)
        parsed = recall.parse_claude_session(str(path), 0, [])
        printed = list(read_session.iter_messages(str(path)))
        self.assertEqual(printed, parsed.messages if parsed else None)
        self.assertEqual(len(printed), 2)

    def test_codex_drops_the_same_injected_blocks(self) -> None:
        entries: list[JSONObject] = [
            {"timestamp": "2026-01-01T00:00:00.000Z", "type": "session_meta",
             "payload": {"id": "abc", "cwd": "/w"}},
            {"timestamp": "2026-01-01T00:01:00.000Z", "type": "response_item",
             "payload": {"role": "user",
                         "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
            {"timestamp": "2026-01-01T00:02:00.000Z", "type": "response_item",
             "payload": {"role": "user",
                         "content": [{"type": "input_text", "text": "a real question"}]}},
        ]
        path = self.write("sessions/rollout-2026-01-01T00-00-00-abc.jsonl", entries)
        parsed = recall.parse_codex_session(str(path), 0, [])
        printed = list(read_session.iter_messages(str(path)))
        self.assertEqual(printed, parsed.messages if parsed else None)
        self.assertEqual(printed, [("user", "a real question")])

    def test_grok_drops_the_same_injected_blocks(self) -> None:
        directory = self.tmp / quote("/w", safe="") / "0199-abc"
        directory.mkdir(parents=True)
        entries: list[JSONObject] = [
            {"type": "user", "content": "<git_status>clean</git_status>"},
            {"type": "user", "content": "harness", "synthetic_reason": "context"},
            {"type": "user", "content": "a real question"},
        ]
        path = directory / "chat_history.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        parsed = recall.parse_grok_session(str(path), 0, [])
        printed = list(read_session.iter_messages(str(path)))
        self.assertEqual(printed, parsed.messages if parsed else None)
        self.assertEqual(printed, [("user", "a real question")])


class FormatDetection(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp: Path = Path(tmp.name)

    def test_claude(self) -> None:
        path = self.tmp / "s.jsonl"
        path.write_text(json.dumps(
            {"parentUuid": None, "type": "user", "message": {"content": "hi"}}) + "\n",
            encoding="utf-8")
        self.assertEqual(read_session.detect_format(str(path)), "claude")

    def test_codex(self) -> None:
        path = self.tmp / "s.jsonl"
        path.write_text(json.dumps(
            {"type": "session_meta", "payload": {"id": "a", "cwd": "/w"}}) + "\n",
            encoding="utf-8")
        self.assertEqual(read_session.detect_format(str(path)), "codex")

    def test_grok_by_filename(self) -> None:
        path = self.tmp / "chat_history.jsonl"
        path.write_text(json.dumps({"type": "user", "content": "hi"}) + "\n",
                        encoding="utf-8")
        self.assertEqual(read_session.detect_format(str(path)), "grok")


if __name__ == "__main__":
    unittest.main()
