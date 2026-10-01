# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DAEMON = Path(
    "/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/Info.plist"
)
REPOSITORY = "https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice.git"


def installed_version() -> str:
    with DAEMON.open("rb") as file:
        version = plistlib.load(file)["CFBundleShortVersionString"]
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


def build(destination: Path, source: Path | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kid-trackpad-build-") as temporary:
        root = Path(temporary)
        if source is None:
            version = installed_version()
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
        metadata = json.loads((source / "version.json").read_text())
        output = root / "kid-trackpad-mouse"
        compile_client(source, output)
        destination.write_bytes(output.read_bytes())
        destination.chmod(0o755)
        destination.with_suffix(".json").write_text(json.dumps(metadata) + "\n")
    print(f"Built {destination} for driver {metadata['package_version']}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-source", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path.home() / ".local/bin/kid-trackpad-mouse"
    )
    arguments = parser.parse_args()
    build(arguments.output, arguments.driver_source)


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("The virtual mouse client builds on macOS only.")
    main()
