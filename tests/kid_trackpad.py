# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import plistlib
import re
import unittest
from pathlib import Path
from typing import final

from scripts.kid_trackpad import (
    LOCK_SOUND,
    POINTER_EVENT_MASK,
    UNLOCK_SOUND,
    follow_layers,
    kanata_address,
    layer_name,
    parse_plist,
)

REPOSITORY = Path(__file__).resolve().parents[1]
KANATA_DAEMON = REPOSITORY / "LaunchDaemons/com.tylerlaprade.kanata.plist"
KID_AGENT = REPOSITORY / "LaunchAgents/com.tylerlaprade.kid-trackpad.plist"
KEY_DOWN = 10
KEY_UP = 11
FLAGS_CHANGED = 12
SYSTEM_DEFINED = 14


def kid_layer() -> str:
    source = (REPOSITORY / ".config/kanata/kanata.kbd").read_text()
    match = re.search(r"\(deflayermap \(kid\)\n(?P<keys>.*?)\n\)", source, re.DOTALL)
    if match is None:
        raise AssertionError("The kid layer is missing.")
    return match["keys"]


@final
class RecordingGate:
    def __init__(self) -> None:
        self.states: list[str] = []

    def block(self) -> None:
        self.states.append("blocked")

    def allow(self) -> None:
        self.states.append("allowed")


@final
class ConfigTest(unittest.TestCase):
    def test_kid_layer_passes_typing_and_keeps_system_keys_blocked(self) -> None:
        keys = kid_layer()
        self.assertIn("q q", keys)
        self.assertIn("m m", keys)
        self.assertIn("f1 brdn", keys)
        self.assertIn("___ XX", keys)
        self.assertNotIn("mctl", keys)
        self.assertNotIn("esc", keys)
        self.assertNotIn("tab", keys)

    def test_kanata_no_longer_toggles_mouse_keys(self) -> None:
        source = (REPOSITORY / ".config/kanata/kanata.kbd").read_text()
        self.assertNotIn("lalt 70 lalt", source)

    def test_event_tap_never_sees_keyboard_or_media_keys(self) -> None:
        for event_type in (KEY_DOWN, KEY_UP, FLAGS_CHANGED, SYSTEM_DEFINED):
            self.assertFalse(POINTER_EVENT_MASK & (1 << event_type))

    def test_lock_and_unlock_sounds_are_distinct_system_sounds(self) -> None:
        self.assertTrue(Path(LOCK_SOUND).is_file())
        self.assertTrue(Path(UNLOCK_SOUND).is_file())
        self.assertNotEqual(LOCK_SOUND, UNLOCK_SOUND)

    def test_agent_finds_the_port_the_kanata_daemon_opens(self) -> None:
        self.assertEqual(
            kanata_address(KANATA_DAEMON.read_bytes()), ("127.0.0.1", 41471)
        )

    def test_kanata_daemon_without_a_port_fails_loudly(self) -> None:
        daemon = plistlib.dumps({"ProgramArguments": ["kanata", "--cfg", "x"]})
        with self.assertRaisesRegex(ValueError, "TCP port"):
            kanata_address(daemon)

    def test_kanata_runs_directly_so_its_own_permissions_apply(self) -> None:
        daemon = parse_plist(KANATA_DAEMON.read_bytes())
        if not isinstance(daemon, dict):
            self.fail("The Kanata daemon is not a property list dictionary.")
        arguments = daemon["ProgramArguments"]
        if not isinstance(arguments, list):
            self.fail("The Kanata daemon has no program arguments.")
        self.assertEqual(arguments[0], "__HOME__/.local/bin/kanata")

    def test_agent_runs_on_apples_python(self) -> None:
        agent = parse_plist(KID_AGENT.read_bytes())
        if not isinstance(agent, dict):
            self.fail("The kid trackpad agent is not a property list dictionary.")
        arguments = agent["ProgramArguments"]
        if not isinstance(arguments, list):
            self.fail("The kid trackpad agent has no program arguments.")
        self.assertEqual(arguments[0], "/usr/bin/python3")


@final
class LayerTest(unittest.TestCase):
    def test_only_layer_changes_move_the_pointer_gate(self) -> None:
        self.assertEqual(layer_name(b'{"LayerChange":{"new":"kid"}}\n'), "kid")
        self.assertIsNone(layer_name(b'{"TapActivated":{"key":"lsft"}}\n'))
        self.assertIsNone(layer_name(b'{"ConfigFileReload":{"new":"a.kbd"}}\n'))
        with self.assertRaises(ValueError):
            layer_name(b'{"Error":{"msg":"unexpected"}}\n')

    def test_lock_unlock_and_disconnect(self) -> None:
        gate = RecordingGate()
        follow_layers(
            [
                b'{"LayerChange":{"new":"base"}}\n',
                b'{"LayerChange":{"new":"kid"}}\n',
                b'{"TapActivated":{"key":"k"}}\n',
                b'{"LayerChange":{"new":"base"}}\n',
            ],
            gate,
        )
        self.assertEqual(gate.states, ["allowed", "blocked", "allowed", "allowed"])

    def test_disconnection_while_locked_restores_the_pointer(self) -> None:
        gate = RecordingGate()
        follow_layers([b'{"LayerChange":{"new":"kid"}}\n'], gate)
        self.assertEqual(gate.states, ["blocked", "allowed"])

    def test_unexpected_message_while_locked_restores_the_pointer(self) -> None:
        gate = RecordingGate()
        with self.assertRaises(ValueError):
            follow_layers(
                [b'{"LayerChange":{"new":"kid"}}\n', b'{"Unknown":{}}\n'], gate
            )
        self.assertEqual(gate.states, ["blocked", "allowed"])


if __name__ == "__main__":
    unittest.main()
