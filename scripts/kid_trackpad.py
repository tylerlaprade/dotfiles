#!/usr/bin/python3
# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
# Apple's stable python3 on purpose: the Accessibility grant this event tap needs
# is keyed to the interpreter's binary, and Homebrew's moves on every release.
from __future__ import annotations

import contextlib
import ctypes
import enum
import json
import plistlib
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Union

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

KANATA_PLIST = Path("/Library/LaunchDaemons/com.tylerlaprade.kanata.plist")
KID_LAYER = "kid"
LOCK_SOUND = "/System/Library/Sounds/Tink.aiff"
UNLOCK_SOUND = "/System/Library/Sounds/Pop.aiff"
IGNORED_MESSAGES = frozenset(
    {"TapActivated", "HoldActivated", "ConfigFileReload", "MessagePush"}
)
RECONNECT_SECONDS = 1
PERMISSION_RETRY_SECONDS = 10
SESSION_EVENT_TAP = 1
HEAD_INSERT_EVENT_TAP = 0
FILTERING_EVENT_TAP = 0


class EventType(enum.IntEnum):
    LEFT_MOUSE_DOWN = 1
    LEFT_MOUSE_UP = 2
    RIGHT_MOUSE_DOWN = 3
    RIGHT_MOUSE_UP = 4
    MOUSE_MOVED = 5
    LEFT_MOUSE_DRAGGED = 6
    RIGHT_MOUSE_DRAGGED = 7
    ROTATE = 18
    BEGIN_GESTURE = 19
    END_GESTURE = 20
    SCROLL_WHEEL = 22
    TABLET_POINTER = 23
    TABLET_PROXIMITY = 24
    OTHER_MOUSE_DOWN = 25
    OTHER_MOUSE_UP = 26
    OTHER_MOUSE_DRAGGED = 27
    GESTURE = 29
    MAGNIFY = 30
    SWIPE = 31
    SMART_MAGNIFY = 32
    QUICK_LOOK = 33
    PRESSURE = 34
    DIRECT_TOUCH = 37
    CHANGE_MODE = 38
    TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
    TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF


TAP_DISABLED = frozenset(
    {EventType.TAP_DISABLED_BY_TIMEOUT, EventType.TAP_DISABLED_BY_USER_INPUT}
)
POINTER_EVENT_MASK = sum(1 << event for event in EventType if event not in TAP_DISABLED)

JSONValue = Union[
    None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]
]
parse_json: Callable[[bytes], JSONValue] = json.loads
PlistValue = Union[
    bool,
    int,
    float,
    str,
    bytes,
    datetime,
    list["PlistValue"],
    dict[str, "PlistValue"],
]
parse_plist: Callable[[bytes], PlistValue] = plistlib.loads

TapCallback = ctypes.CFUNCTYPE(
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p
)
graphics = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
foundation = ctypes.CDLL(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
graphics.CGEventTapCreate.restype = ctypes.c_void_p
graphics.CGEventTapCreate.argtypes = (
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint64,
    TapCallback,
    ctypes.c_void_p,
)
graphics.CGEventTapEnable.restype = None
graphics.CGEventTapEnable.argtypes = (ctypes.c_void_p, ctypes.c_bool)
foundation.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
foundation.CFMachPortCreateRunLoopSource.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_long,
)
foundation.CFMachPortInvalidate.restype = None
foundation.CFMachPortInvalidate.argtypes = (ctypes.c_void_p,)
foundation.CFRunLoopGetCurrent.restype = ctypes.c_void_p
foundation.CFRunLoopGetCurrent.argtypes = ()
foundation.CFRunLoopAddSource.restype = None
foundation.CFRunLoopAddSource.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
foundation.CFRunLoopRun.restype = None
foundation.CFRunLoopRun.argtypes = ()
foundation.CFRunLoopStop.restype = None
foundation.CFRunLoopStop.argtypes = (ctypes.c_void_p,)
foundation.CFRelease.restype = None
foundation.CFRelease.argtypes = (ctypes.c_void_p,)
create_event_tap: Callable[[int, int, int, int, object, None], int | None] = (
    graphics.CGEventTapCreate
)
enable_event_tap: Callable[[int, bool], None] = graphics.CGEventTapEnable
create_run_loop_source: Callable[[None, int, int], int] = (
    foundation.CFMachPortCreateRunLoopSource
)
invalidate_mach_port: Callable[[int], None] = foundation.CFMachPortInvalidate
current_run_loop: Callable[[], int] = foundation.CFRunLoopGetCurrent
add_run_loop_source: Callable[[int, int, int], None] = foundation.CFRunLoopAddSource
run_run_loop: Callable[[], None] = foundation.CFRunLoopRun
stop_run_loop: Callable[[int], None] = foundation.CFRunLoopStop
release: Callable[[int], None] = foundation.CFRelease
default_run_loop_mode = ctypes.c_void_p.in_dll(foundation, "kCFRunLoopDefaultMode")


def play(sound: str) -> None:
    subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            'for _ in 1 2 3; do /usr/bin/afplay "$0"; sleep 0.1; done',
            sound,
        ]
    )


class PointerGate(Protocol):
    def block(self) -> None: ...

    def allow(self) -> None: ...


class PointerBlocker:
    def __init__(self) -> None:
        self.blocking: bool = False
        self.callback: object = TapCallback(self.filter)
        tap = create_event_tap(
            SESSION_EVENT_TAP,
            HEAD_INSERT_EVENT_TAP,
            FILTERING_EVENT_TAP,
            POINTER_EVENT_MASK,
            self.callback,
            None,
        )
        if tap is None:
            raise PermissionError(
                "macOS refused the pointer event tap. Allow python3 in System Settings → Privacy & Security → Accessibility."
            )
        self.tap: int = tap
        enable_event_tap(self.tap, self.blocking)
        self.run_loop: int | None = None
        listening = threading.Event()
        self.thread: threading.Thread = threading.Thread(
            target=self.listen, args=(listening,), name="pointer-event-tap", daemon=True
        )
        self.thread.start()
        listening.wait()

    def listen(self, listening: threading.Event) -> None:
        self.run_loop = current_run_loop()
        source = create_run_loop_source(None, self.tap, 0)
        add_run_loop_source(self.run_loop, source, default_run_loop_mode.value or 0)
        release(source)
        listening.set()
        run_run_loop()

    def filter(
        self, _proxy: int | None, event_type: int, event: int | None, _info: int | None
    ) -> int | None:
        # Re-enabling only while locked: re-disabling an unlocked tap posts another disabled event.
        if event_type in TAP_DISABLED:
            if self.blocking:
                enable_event_tap(self.tap, self.blocking)
            return event
        return None if self.blocking else event

    def block(self) -> None:
        if self.blocking:
            return
        self.blocking = True
        enable_event_tap(self.tap, self.blocking)
        play(LOCK_SOUND)
        print("Kid mode locked: pointer input blocked.", flush=True)

    def allow(self) -> None:
        if not self.blocking:
            return
        self.blocking = False
        enable_event_tap(self.tap, self.blocking)
        play(UNLOCK_SOUND)
        print("Kid mode unlocked: pointer input restored.", flush=True)

    def close(self) -> None:
        self.allow()
        invalidate_mach_port(self.tap)
        if self.run_loop is not None:
            stop_run_loop(self.run_loop)
        self.thread.join()
        release(self.tap)


def layer_name(message: bytes) -> str | None:
    data = parse_json(message)
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError(f"Unexpected Kanata message: {data!r}")
    ((kind, event),) = data.items()
    if kind in IGNORED_MESSAGES:
        return None
    if kind != "LayerChange":
        raise ValueError(f"Unexpected Kanata message: {data!r}")
    layer = event.get("new") if isinstance(event, dict) else None
    if not isinstance(layer, str):
        raise TypeError(f"Invalid Kanata layer: {event!r}")
    return layer


def follow_layers(messages: Iterable[bytes], pointer: PointerGate) -> None:
    try:
        for message in messages:
            layer = layer_name(message)
            if layer == KID_LAYER:
                pointer.block()
            elif layer is not None:
                pointer.allow()
    finally:
        pointer.allow()


def kanata_address(plist: bytes) -> tuple[str, int]:
    data = parse_plist(plist)
    arguments = data.get("ProgramArguments") if isinstance(data, dict) else None
    if not isinstance(arguments, list) or "--port" not in arguments:
        raise ValueError("The Kanata launch daemon does not open a TCP port.")
    address = arguments[arguments.index("--port") + 1]
    if not isinstance(address, str):
        raise TypeError(f"Invalid Kanata port: {address!r}")
    host, port = address.rsplit(":", 1)
    return host, int(port)


def try_blocking() -> PointerBlocker | None:
    try:
        return PointerBlocker()
    except PermissionError as error:
        print(error, flush=True)
        return None


def wait_for_permission() -> PointerBlocker:
    while (pointer := try_blocking()) is None:
        time.sleep(PERMISSION_RETRY_SECONDS)
    return pointer


def try_connect(address: tuple[str, int]) -> socket.socket | None:
    try:
        return socket.create_connection(address)
    except ConnectionRefusedError:
        return None


def main() -> None:
    with contextlib.closing(wait_for_permission()) as pointer:
        address = kanata_address(KANATA_PLIST.read_bytes())
        print(f"Following Kanata at {address[0]}:{address[1]}.", flush=True)
        while True:
            connection = try_connect(address)
            if connection is None:
                time.sleep(RECONNECT_SECONDS)
                continue
            with connection, connection.makefile("rb") as messages:
                follow_layers(messages, pointer)
            print("Kanata disconnected; pointer input restored.", flush=True)


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("Kid mode pointer blocking requires macOS.")
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit(str(error)) from error
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
