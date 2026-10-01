# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, final, override
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Sequence

from scripts.kid_trackpad import (
    REPOSITORY,
    SERVICE,
    SERVICE_PLIST,
    VirtualMouse,
    follow_layers,
    layer_name,
    main,
    prototype_config,
)


@final
class ConfigTest(unittest.TestCase):
    def test_current_config_keeps_modifiers_and_function_keys_blocked(self) -> None:
        source = (REPOSITORY / ".config/kanata/kanata.kbd").read_text()
        result = prototype_config(source)
        self.assertNotIn("(macro 120 lalt", result)
        self.assertIn("  ___ XX", result)
        self.assertIn("  i i", result)
        self.assertIn("  m m", result)
        self.assertIn("  f1 brdn", result)
        self.assertNotIn("  f3 mctl", result)

    def test_changed_toggle_fails_instead_of_leaving_mouse_keys_enabled(self) -> None:
        source = (REPOSITORY / ".config/kanata/kanata.kbd").read_text()
        with self.assertRaisesRegex(ValueError, "toggle changed"):
            prototype_config(source.replace("macro 120 lalt", "macro 150 lalt"))

    def test_only_layer_events_change_the_mouse(self) -> None:
        self.assertEqual(layer_name(b'{"LayerChange":{"new":"kid"}}\n'), "kid")
        self.assertIsNone(layer_name(b'{"TapActivated":{"key":"lsft"}}\n'))
        with self.assertRaises(ValueError):
            layer_name(b'{"ConfigFileReload":{"new":"another.kbd"}}\n')

    def test_failed_prototype_restores_the_normal_service(self) -> None:
        async def failed_run(_kanata: Path, _client: Path, _config: Path) -> None:
            raise RuntimeError("driver failed")

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".config/kanata/kanata.kbd"
            config.parent.mkdir(parents=True)
            config.write_text((REPOSITORY / ".config/kanata/kanata.kbd").read_text())
            with (
                patch.object(sys, "argv", ["kid-trackpad", "--home", str(home)]),
                patch("scripts.kid_trackpad.preflight"),
                patch("scripts.kid_trackpad.run_prototype", failed_run),
                patch(
                    "scripts.kid_trackpad.run_command",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as command,
                self.assertRaisesRegex(RuntimeError, "driver failed"),
            ):
                main()
            self.assertEqual(
                command.call_args_list[1].args, ("launchctl", "bootout", SERVICE)
            )
            self.assertEqual(
                command.call_args_list[2].args,
                ("launchctl", "bootstrap", "system", SERVICE_PLIST),
            )


@final
class ObservedMouse(VirtualMouse):
    def __init__(self, command: Sequence[str]) -> None:
        super().__init__(command)
        self.locked = asyncio.Event()
        self.unlocked = asyncio.Event()

    @override
    async def lock(self) -> None:
        await super().lock()
        self.locked.set()

    @override
    async def unlock(self) -> None:
        await super().unlock()
        self.unlocked.set()


@final
class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.mouse = ObservedMouse(
            [
                sys.executable,
                "-u",
                "-c",
                'import sys; print("READY", flush=True); sys.stdin.read()',
            ]
        )
        self.reader = asyncio.StreamReader()

    @override
    async def asyncTearDown(self) -> None:
        await self.mouse.unlock()

    async def test_lock_unlock_and_disconnect_cleanup(self) -> None:
        watcher = asyncio.create_task(follow_layers(self.reader, self.mouse))
        self.reader.feed_data(b'{"LayerChange":{"new":"kid"}}\n')
        async with asyncio.timeout(3):
            await self.mouse.locked.wait()
        process = self.mouse.process
        self.assertIsNotNone(process)
        self.reader.feed_data(b'{"LayerChange":{"new":"base"}}\n')
        async with asyncio.timeout(3):
            await self.mouse.unlocked.wait()
        self.reader.feed_eof()
        with self.assertRaises(ConnectionError):
            await watcher
        if process is None:
            self.fail("The mouse client never started.")
        self.assertEqual(process.returncode, 0)

    async def test_disconnection_while_locked_removes_the_client(self) -> None:
        watcher = asyncio.create_task(follow_layers(self.reader, self.mouse))
        self.reader.feed_data(b'{"LayerChange":{"new":"kid"}}\n')
        async with asyncio.timeout(3):
            await self.mouse.locked.wait()
        process = self.mouse.process
        self.reader.feed_eof()
        with self.assertRaises(ConnectionError):
            await watcher
        self.assertIsNone(self.mouse.process)
        if process is None:
            self.fail("The mouse client never started.")
        self.assertEqual(process.returncode, 0)

    async def test_driver_failure_aborts_without_waiting_for_another_key(self) -> None:
        self.mouse = ObservedMouse(
            [
                sys.executable,
                "-u",
                "-c",
                'import sys; print("READY", flush=True); print("ERROR driver lost", flush=True); sys.stdin.read()',
            ]
        )
        self.reader.feed_data(b'{"LayerChange":{"new":"kid"}}\n')
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await asyncio.wait_for(follow_layers(self.reader, self.mouse), timeout=3)
        self.assertIsNone(self.mouse.process)

    async def test_failed_startup_closes_the_client(self) -> None:
        self.mouse = ObservedMouse(
            [
                sys.executable,
                "-u",
                "-c",
                'import sys; print("ERROR wrong driver", flush=True); sys.stdin.read()',
            ]
        )
        self.reader.feed_data(b'{"LayerChange":{"new":"kid"}}\n')
        with self.assertRaisesRegex(RuntimeError, "wrong driver"):
            await asyncio.wait_for(follow_layers(self.reader, self.mouse), timeout=3)
        self.assertIsNone(self.mouse.process)

    async def test_canceling_the_prototype_removes_the_mouse(self) -> None:
        watcher = asyncio.create_task(follow_layers(self.reader, self.mouse))
        self.reader.feed_data(b'{"LayerChange":{"new":"kid"}}\n')
        async with asyncio.timeout(3):
            await self.mouse.locked.wait()
        process = self.mouse.process
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        self.assertIsNone(self.mouse.process)
        if process is None:
            self.fail("The mouse client never started.")
        self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
