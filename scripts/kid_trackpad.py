# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import plistlib
import pwd
import re
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPOSITORY = Path(__file__).resolve().parents[1]
DAEMON_INFO = Path(
    "/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/Info.plist"
)
SERVICE = "system/com.tylerlaprade.kanata"
SERVICE_PLIST = "/Library/LaunchDaemons/com.tylerlaprade.kanata.plist"
TEXT_KEYS = [
    "grv",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "0",
    "-",
    "=",
    "q",
    "w",
    "e",
    "r",
    "t",
    "y",
    "u",
    "i",
    "o",
    "p",
    "[",
    "]",
    "\\",
    "a",
    "s",
    "d",
    "f",
    "g",
    "h",
    "j",
    "l",
    ";",
    "'",
    "z",
    "x",
    "c",
    "v",
    "b",
    "n",
    "m",
    ",",
    ".",
    "/",
]
MOUSE_KEYS_MACRO = "(macro 120 lalt 70 lalt 70 lalt 70 lalt 70 lalt)"
MOUSE_KEYS_TOGGLE_COUNT = 2


def prototype_config(source: str) -> str:
    if source.count(MOUSE_KEYS_MACRO) != MOUSE_KEYS_TOGGLE_COUNT:
        raise ValueError(
            "The Mouse Keys toggle changed; update the prototype generator."
        )
    result = source.replace(MOUSE_KEYS_MACRO, "")
    pattern = r"\(deflayermap \(kid\)\n(?P<keys>.*?)\n\)"
    match = re.search(pattern, result, re.DOTALL)
    if match is None or "  k @kid-k\n  ___ XX" not in match["keys"]:
        raise ValueError("The kid layer changed; update the prototype generator.")
    keys = match["keys"].replace(
        "  k @kid-k", "\n".join(f"  {key} {key}" for key in TEXT_KEYS) + "\n  k @kid-k"
    )
    result = result[: match.start("keys")] + keys + result[match.end("keys") :]
    fallback = "(on-press tap-vkey vk-base) break\n    () XX break"
    if result.count(fallback) != 1:
        raise ValueError("The unlock action changed; update the prototype generator.")
    return result.replace(fallback, "(on-press tap-vkey vk-base) break\n    () k break")


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


class VirtualMouse:
    def __init__(self, command: Sequence[str]) -> None:
        self.command = command
        self.process: asyncio.subprocess.Process | None = None
        self.failure: asyncio.Task[bytes] | None = None

    async def lock(self) -> None:
        if self.process is not None:
            return
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self.process = process
        if process.stdout is None:
            raise RuntimeError("The virtual mouse has no status channel.")
        ready = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        if ready != b"READY\n":
            raise RuntimeError(
                f"Virtual mouse failed: {ready.decode().strip() or 'client exited'}"
            )
        self.failure = asyncio.create_task(process.stdout.readline())
        print("Kid mode: virtual mouse connected; Mouse Keys remains off.", flush=True)

    async def unlock(self) -> None:
        process = self.process
        self.process = None
        if self.failure is not None:
            self.failure.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.failure
            self.failure = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            await stop_process(process)
        if process.returncode != 0:
            raise RuntimeError(
                f"Virtual mouse exited with status {process.returncode}."
            )
        print("Kid mode unlocked: virtual mouse removed.", flush=True)


def layer_name(message: bytes) -> str | None:
    data = json.loads(message)
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError(f"Unexpected Kanata message: {data!r}")
    if set(data) in ({"TapActivated"}, {"HoldActivated"}):
        return None
    if set(data) != {"LayerChange"}:
        raise ValueError(f"Unexpected Kanata message: {data!r}")
    event = data["LayerChange"]
    if not isinstance(event, dict) or not isinstance(event.get("new"), str):
        raise TypeError(f"Invalid Kanata layer: {event!r}")
    return event["new"]


async def follow_layers(reader: asyncio.StreamReader, mouse: VirtualMouse) -> None:
    try:
        while True:
            change = asyncio.create_task(reader.readline())
            try:
                watches = {change}
                if mouse.failure is not None:
                    watches.add(mouse.failure)
                finished, _ = await asyncio.wait(
                    watches, return_when=asyncio.FIRST_COMPLETED
                )
                if mouse.failure in finished:
                    raise RuntimeError(
                        "The virtual mouse disconnected while kid mode was active."
                    )
                message = await change
                if not message:
                    raise ConnectionError("Kanata disconnected.")
                layer = layer_name(message)
                if layer is None:
                    continue
                if layer == "kid":
                    await mouse.lock()
                else:
                    await mouse.unlock()
            finally:
                change.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await change
    finally:
        await mouse.unlock()


async def try_connect(
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    try:
        return await asyncio.open_connection("127.0.0.1", port)
    except ConnectionRefusedError:
        return None


async def connect_kanata(
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    async with asyncio.timeout(10):
        while True:
            connection = await try_connect(port)
            if connection is not None:
                return connection
            await asyncio.sleep(0.1)


async def run_prototype(kanata: Path, client: Path, config: Path) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        str(kanata),
        "--no-wait",
        "--cfg",
        str(config),
        "--port",
        f"127.0.0.1:{port}",
        start_new_session=True,
    )
    mouse = VirtualMouse([str(client)])
    try:
        reader, writer = await connect_kanata(port)
        try:
            await follow_layers(reader, mouse)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await stop_process(process)


def run_command(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def preflight(home: Path, kanata: Path, client: Path, config: Path) -> None:
    if not kanata.is_file() or not client.is_file():
        raise ValueError(
            "Run install.sh --kid-trackpad-prototype before starting the prototype."
        )
    with DAEMON_INFO.open("rb") as file:
        driver_version = plistlib.load(file)["CFBundleShortVersionString"]
    built_version = json.loads(client.with_suffix(".json").read_text())[
        "package_version"
    ]
    if driver_version != built_version:
        raise ValueError(
            "The virtual HID driver changed. Run install.sh --kid-trackpad-prototype to rebuild."
        )
    run_command(str(kanata), "--check", "--cfg", str(config))
    setting = run_command(
        "sudo",
        "-u",
        pwd.getpwuid(home.stat().st_uid).pw_name,
        "defaults",
        "read",
        "com.apple.AppleMultitouchTrackpad",
        "USBMouseStopsTrackpad",
    ).stdout.strip()
    if setting != "1":
        raise ValueError(
            "Enable “Ignore built-in trackpad when mouse or wireless trackpad is present” in Accessibility → Pointer Control first."
        )
    enabled = run_command(
        "sudo",
        "-u",
        pwd.getpwuid(home.stat().st_uid).pw_name,
        "defaults",
        "read",
        "com.apple.universalaccess",
        "mouseDriver",
        check=False,
    )
    if enabled.returncode == 0 and enabled.stdout.strip() == "1":
        raise ValueError("Turn Mouse Keys off before starting the prototype.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the temporary virtual-mouse kid-mode prototype."
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(
            pwd.getpwnam(
                os.environ.get("SUDO_USER", pwd.getpwuid(os.getuid()).pw_name)
            ).pw_dir
        ),
    )
    arguments = parser.parse_args()
    home = arguments.home.resolve()
    kanata = home / ".local/bin/kanata"
    client = home / ".local/bin/kid-trackpad-mouse"
    with tempfile.TemporaryDirectory(prefix="kid-trackpad-") as temporary:
        config = Path(temporary) / "kanata.kbd"
        config.write_text(
            prototype_config((home / ".config/kanata/kanata.kbd").read_text())
        )
        preflight(home, kanata, client, config)
        active = run_command("launchctl", "print", SERVICE, check=False).returncode == 0
        if active:
            run_command("launchctl", "bootout", SERVICE)
        try:
            print(
                "Prototype running. Both Shifts + K locks; either Shift + K unlocks. Ctrl+C restores your normal service.",
                flush=True,
            )
            asyncio.run(run_prototype(kanata, client, config))
        finally:
            if active:
                run_command("launchctl", "bootstrap", "system", SERVICE_PLIST)
                print("Restored the normal Kanata service.", flush=True)


if __name__ == "__main__":
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise SystemExit("Run this prototype on macOS with sudo.")
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        main()
    except (
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        TimeoutError,
        subprocess.CalledProcessError,
    ) as error:
        raise SystemExit(str(error)) from error
    except KeyboardInterrupt:
        print("Prototype stopped.")
