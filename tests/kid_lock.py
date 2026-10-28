# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
import plistlib
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast, final, override

REPOSITORY = Path(__file__).resolve().parents[1]
KID_LOCK = REPOSITORY / "scripts/bin/kid-lock.sh"
LOCKED = 1
UNKNOWN = 2


def layer_change(layer: str) -> bytes:
    return json.dumps({"LayerChange": {"new": layer}}).encode() + b"\n"


@final
class FakeKanata:
    def __init__(self, messages: list[bytes]) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.server.getsockname()[1]
        self.messages = messages
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        connection = self.server.accept()[0]
        with connection:
            for message in self.messages:
                connection.sendall(message)

    def close(self) -> None:
        self.thread.join(timeout=5)
        self.server.close()


@final
class KidLockTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plist = Path(directory.name) / "kanata.plist"

    def point_at(self, port: int) -> None:
        arguments = ["kanata", "--port", f"127.0.0.1:{port}"]
        self.plist.write_bytes(plistlib.dumps({"ProgramArguments": arguments}))

    def run_kid_lock(self, *arguments: str) -> int:
        environment = {"KID_LOCK_KANATA_PLIST": str(self.plist), "PATH": "/opt/homebrew/bin:/usr/bin:/bin"}
        return subprocess.run(
            [str(KID_LOCK), *arguments], env=environment, capture_output=True, timeout=10, check=False
        ).returncode

    def answer(self, messages: list[bytes], *arguments: str) -> int:
        kanata = FakeKanata(messages)
        self.addCleanup(kanata.close)
        self.point_at(kanata.port)
        return self.run_kid_lock(*arguments)

    def test_base_layer_is_unlocked(self) -> None:
        self.assertEqual(self.answer([layer_change("base")]), 0)

    def test_kid_layer_is_locked(self) -> None:
        self.assertEqual(self.answer([layer_change("kid")]), LOCKED)

    def test_messages_other_than_layer_changes_are_skipped(self) -> None:
        self.assertEqual(self.answer([b'{"MessagePush":{"message":"x"}}\n', layer_change("kid")]), LOCKED)

    def test_wait_returns_once_kid_mode_turns_off(self) -> None:
        self.assertEqual(self.answer([layer_change("kid"), layer_change("kid"), layer_change("base")], "--wait"), 0)

    def test_kanata_not_listening_means_no_kid_mode(self) -> None:
        with socket.create_server(("127.0.0.1", 0)) as closed:
            port = cast("int", closed.getsockname()[1])
        self.point_at(port)
        self.assertEqual(self.run_kid_lock(), 0)

    def test_unreadable_daemon_settings_are_unknown(self) -> None:
        self.assertEqual(self.run_kid_lock(), UNKNOWN)

    def test_kanata_closing_before_naming_a_layer_is_unknown(self) -> None:
        self.assertEqual(self.answer([]), UNKNOWN)


if __name__ == "__main__":
    unittest.main()
