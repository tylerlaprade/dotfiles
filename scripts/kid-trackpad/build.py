# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

DAEMON = Path(
    "/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/Info.plist"
)
REPOSITORY = "https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice.git"

type JSONValue = (
    bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None
)
type PlistValue = (
    bool
    | int
    | float
    | str
    | bytes
    | datetime
    | plistlib.UID
    | list[PlistValue]
    | dict[str, PlistValue]
)
parse_json: Callable[[str], JSONValue] = json.loads
parse_plist: Callable[[bytes], PlistValue] = plistlib.loads


def json_object(value: JSONValue) -> dict[str, JSONValue]:
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected a JSON object, got {value!r}")


def plist_dictionary(value: PlistValue) -> dict[str, PlistValue]:
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected a property list dictionary, got {value!r}")


def installed_version() -> str:
    version = plist_dictionary(parse_plist(DAEMON.read_bytes()))[
        "CFBundleShortVersionString"
    ]
    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError(f"Unrecognized virtual HID daemon version: {version!r}")
    return version


def compile_client(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++23",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            "-isystem",
            str(source / "include"),
            "-isystem",
            str(source / "vendor/vendor/include"),
            "-framework",
            "CoreFoundation",
            "-framework",
            "IOKit",
            str(Path(__file__).with_name("virtual-mouse.cpp")),
            "-o",
            str(destination),
        ],
        check=True,
    )


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for source in (Path(__file__), Path(__file__).with_name("virtual-mouse.cpp")):
        digest.update(source.read_bytes())
    return digest.hexdigest()


def is_current(destination: Path, version: str) -> bool:
    if not destination.is_file() or not os.access(destination, os.X_OK):
        return False
    try:
        metadata = parse_json(destination.with_suffix(".json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return (
        isinstance(metadata, dict)
        and metadata.get("package_version") == version
        and metadata.get("source_sha256") == source_fingerprint()
    )


def build(destination: Path, source: Path | None = None) -> None:
    version = installed_version() if source is None else None
    if version is not None and is_current(destination, version):
        return
    print("Building the kid-mode virtual mouse helper…", flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kid-trackpad-build-") as temporary:
        root = Path(temporary)
        if source is None:
            source = root / "driver"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    f"v{version}",
                    REPOSITORY,
                    str(source),
                ],
                check=True,
            )
        metadata = json_object(parse_json((source / "version.json").read_text()))
        output = root / "kid-trackpad-mouse"
        compile_client(source, output)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as staging:
            staged = Path(staging.name)
        try:
            staged.write_bytes(output.read_bytes())
            staged.chmod(0o755)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)
        metadata["source_sha256"] = source_fingerprint()
        destination.with_suffix(".json").write_text(json.dumps(metadata) + "\n")
    print(f"Built {destination} for driver {metadata['package_version']}.")


class Arguments(argparse.Namespace):
    driver_source: Path | None
    output: Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-source", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path.home() / ".local/bin/kid-trackpad-mouse"
    )
    arguments = parser.parse_args(namespace=Arguments())
    build(arguments.output, arguments.driver_source)


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("The virtual mouse client builds on macOS only.")
    main()
