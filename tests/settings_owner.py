# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
"""Runs the settings syncs on fake Macs, so nothing reads or writes this one's settings."""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast, final, override

REPOSITORY = Path(__file__).resolve().parents[1]
APPLE_PYTHON = "/usr/bin/python3"
DOMAIN = "com.example.app"

FAKE_DEFAULTS = f"""#!{sys.executable}
import os, plistlib, sys
from pathlib import Path
live = Path(os.environ["LIVE"])
arguments = [argument for argument in sys.argv[1:] if argument != "-currentHost"]
command = arguments[0]
if command == "domains":
    print(", ".join(sorted(path.stem for path in live.glob("*.plist"))))
    sys.exit()
path = live / (arguments[1] + ".plist")
values = plistlib.loads(path.read_bytes()) if path.exists() else {{}}
if command == "export":
    if not path.exists():
        sys.exit(1)
    sys.stdout.buffer.write(path.read_bytes())
elif command == "write":
    _, _, key, kind, value = arguments
    readers = {{"-bool": lambda text: text == "true", "-int": int, "-float": float, "-string": str}}
    values[key] = readers[kind](value)
    path.write_bytes(plistlib.dumps(values))
elif command == "import":
    values.update(plistlib.loads(Path(arguments[2]).read_bytes()))
    path.write_bytes(plistlib.dumps(values))
else:
    sys.exit(2)
"""

FAKE_OSASCRIPT = f"""#!{sys.executable}
import os, re, sys
from pathlib import Path
live = Path(os.environ["LIVE"]) / "login-items"
script = sys.argv[2]
items = live.read_text().split() if live.exists() else []
if "make login item" in script:
    if os.environ.get("REFUSE_LOGIN_ITEMS"):
        sys.exit("refused")
    items.append(re.search('path:"([^"]+)"', script)[1])
elif "delete" in script:
    if os.environ.get("REFUSE_LOGIN_ITEMS"):
        sys.exit("refused")
    name = re.search('name is "([^"]+)"', script)[1]
    items = [item for item in items if Path(item).stem != name]
else:
    print("".join(f"{{Path(item).stem}}\\n{{item}}\\n" for item in items), end="")
live.write_text("\\n".join(items))
"""


@final
class Mac:
    def __init__(self, root: Path, host_name: str, role: str) -> None:
        self.repo = root / host_name / "repo"
        self.home = root / host_name / "home"
        self.live = root / host_name / "live"
        self.apps = root / "Applications"
        self.fakes = root / host_name / "fakes"
        for folder in (self.home, self.live, self.fakes, self.repo / "scripts/sync", self.repo / "scripts/bin"):
            folder.mkdir(parents=True)
        setup = self.repo / "scripts/setup"
        for name in ("sync-macos-defaults.py", "sync-browser-local-state.py", "threeway.py"):
            shutil.copy(REPOSITORY / "scripts/sync" / name, self.repo / "scripts/sync" / name)
        shutil.copy(REPOSITORY / "scripts/bin/machine-role.sh", self.repo / "scripts/bin/machine-role.sh")
        (setup / "macos-defaults").mkdir(parents=True)
        (setup / "browser-local-state").mkdir()
        (setup / "macos-defaults.conf").write_text("# rules\n")
        (setup / "machines.conf").write_text(f"{host_name} {role}\n")
        for name, body in (
            ("defaults", FAKE_DEFAULTS),
            ("osascript", FAKE_OSASCRIPT),
            ("scutil", f"#!/bin/bash\necho {host_name}\n"),
            ("pgrep", "#!/bin/bash\nexit 1\n"),
            ("killall", "#!/bin/bash\nexit 0\n"),
        ):
            (self.fakes / name).write_text(body)
            (self.fakes / name).chmod(0o755)

    def sync(self, script: str = "sync-macos-defaults.py", *arguments: str, **environment: str) -> str:
        result = subprocess.run(
            [APPLE_PYTHON, str(self.repo / "scripts/sync" / script), *arguments],
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": f"{self.fakes}:/usr/bin:/bin",
                "HOME": str(self.home),
                "LIVE": str(self.live),
                "PYTHONDONTWRITEBYTECODE": "1",
                **environment,
            },
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return result.stdout

    def set_live(self, domain: str, **values: object) -> None:
        (self.live / f"{domain}.plist").write_bytes(plistlib.dumps({**self.get_live(domain), **values}))

    def get_live(self, domain: str) -> dict[str, object]:
        path = self.live / f"{domain}.plist"
        return cast("dict[str, object]", plistlib.loads(path.read_bytes())) if path.exists() else {}

    def set_repo(self, domain: str, **values: str) -> None:
        entries = {key: {"type": "string", "value": value} for key, value in values.items()}
        path = self.repo / "scripts/setup/macos-defaults" / f"{domain}.json"
        path.write_text(json.dumps(entries, indent=2) + "\n")

    def get_repo(self, domain: str) -> dict[str, str]:
        path = self.repo / "scripts/setup/macos-defaults" / f"{domain}.json"
        entries = cast("dict[str, dict[str, str]]", json.loads(path.read_text()))
        return {key: entry["value"] for key, entry in entries.items()}

    def set_repo_login_items(self, *names: str) -> None:
        items = [{"name": name, "path": self.app(name)} for name in names]
        (self.repo / "scripts/setup/login-items.json").write_text(json.dumps(items, indent=2) + "\n")

    def repo_login_items(self) -> list[str]:
        items = cast("list[dict[str, str]]", json.loads((self.repo / "scripts/setup/login-items.json").read_text()))
        return [item["name"] for item in items]

    def set_login_items(self, *names: str) -> None:
        (self.live / "login-items").write_text("\n".join(self.app(name) for name in names))

    def login_items(self) -> list[str]:
        return sorted(Path(item).stem for item in (self.live / "login-items").read_text().split())

    def app(self, name: str) -> str:
        path = self.apps / f"{name}.app"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def working_copy(self) -> dict[str, bytes]:
        return {str(path.relative_to(self.repo)): path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}


class SettingsTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.laptop: Mac = Mac(root, "Laptop", "personal")
        self.host: Mac = Mac(root, "Host", "agent")


@final
class FollowerTest(SettingsTest):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.host.set_repo(DOMAIN, theme="dark", size="18")
        self.host.set_live(DOMAIN, theme="light", size="12")
        self.host.set_repo_login_items("Rex")
        self.host.set_login_items()

    def test_a_new_follower_takes_every_repo_value(self) -> None:
        before = self.host.working_copy()
        self.host.sync()
        self.assertEqual(self.host.get_live(DOMAIN), {"theme": "dark", "size": "18"})
        self.assertEqual(self.host.login_items(), ["Rex"])
        self.assertEqual(self.host.working_copy(), before)

    def test_its_own_change_stays_and_never_reaches_the_repo(self) -> None:
        self.host.sync()
        before = self.host.working_copy()
        self.host.set_live(DOMAIN, size="14", extra="local")
        for _ in range(2):
            self.host.sync()
            self.assertEqual(self.host.get_live(DOMAIN), {"theme": "dark", "size": "14", "extra": "local"})
            self.assertEqual(self.host.working_copy(), before)

    def test_the_owners_next_change_arrives_beside_its_own(self) -> None:
        self.host.sync()
        self.host.set_live(DOMAIN, size="14")
        self.host.sync()
        self.host.set_repo(DOMAIN, theme="solarized", size="20")
        self.host.sync()
        self.assertEqual(self.host.get_live(DOMAIN), {"theme": "solarized", "size": "14"})

    def test_adopt_puts_back_every_repo_value(self) -> None:
        self.host.sync()
        self.host.set_live(DOMAIN, size="14")
        self.host.sync()
        before = self.host.working_copy()
        self.host.sync("sync-macos-defaults.py", "--adopt")
        self.assertEqual(self.host.get_live(DOMAIN), {"theme": "dark", "size": "18"})
        self.assertEqual(self.host.working_copy(), before)

    def test_an_app_only_it_has_is_not_exported(self) -> None:
        before = self.host.working_copy()
        self.host.set_live("com.example.other", theme="light")
        self.host.sync()
        self.assertEqual(self.host.working_copy(), before)

    def test_an_app_it_lacks_is_left_alone(self) -> None:
        self.host.set_repo("com.example.laptop", theme="dark")
        before = self.host.working_copy()
        self.host.sync()
        self.assertEqual(self.host.get_live("com.example.laptop"), {})
        self.assertEqual(self.host.working_copy(), before)

    def test_a_binary_value_is_not_rewritten_in_the_repo(self) -> None:
        self.host.set_live(DOMAIN, blob={"data": b"host"})
        before = self.host.working_copy()
        self.host.sync()
        self.assertEqual(self.host.working_copy(), before)

    def test_its_own_login_item_changes_stay(self) -> None:
        self.host.sync()
        self.host.set_login_items("Chrome")
        before = self.host.working_copy()
        for _ in range(2):
            self.host.sync()
            self.assertEqual(self.host.login_items(), ["Chrome"])
            self.assertEqual(self.host.working_copy(), before)

    def test_the_owners_login_item_changes_arrive(self) -> None:
        self.host.sync()
        self.host.set_repo_login_items("Chrome")
        self.host.sync()
        self.assertEqual(self.host.login_items(), ["Chrome"])

    def test_a_refused_login_item_change_is_retried(self) -> None:
        self.host.sync(REFUSE_LOGIN_ITEMS="1")
        self.assertEqual(self.host.login_items(), [])
        self.host.sync()
        self.assertEqual(self.host.login_items(), ["Rex"])
        self.host.set_repo_login_items("Chrome")
        self.host.sync(REFUSE_LOGIN_ITEMS="1")
        self.assertEqual(self.host.login_items(), ["Rex"])
        self.host.sync()
        self.assertEqual(self.host.login_items(), ["Chrome"])


@final
class OwnerTest(SettingsTest):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.laptop.set_repo(DOMAIN, theme="dark", size="18")
        self.laptop.set_live(DOMAIN, theme="dark", size="18")
        self.laptop.set_repo_login_items("Rex")
        self.laptop.set_login_items("Rex")
        self.laptop.sync()

    def test_its_change_reaches_the_repo(self) -> None:
        self.laptop.set_live(DOMAIN, size="14", extra="new")
        self.laptop.sync()
        self.assertEqual(self.laptop.get_repo(DOMAIN), {"theme": "dark", "size": "14", "extra": "new"})

    def test_a_new_app_reaches_the_repo(self) -> None:
        self.laptop.set_live("com.example.other", theme="light")
        self.laptop.sync()
        self.assertEqual(self.laptop.get_repo("com.example.other"), {"theme": "light"})

    def test_its_login_item_changes_reach_the_repo(self) -> None:
        self.laptop.set_login_items("Chrome")
        self.laptop.sync()
        self.assertEqual(self.laptop.repo_login_items(), ["Chrome"])

    def test_a_binary_value_reaches_the_repo(self) -> None:
        self.laptop.set_live(DOMAIN, blob={"data": b"laptop"})
        self.laptop.sync()
        exported = self.laptop.repo / f"scripts/setup/macos-plists/{DOMAIN}.blob.plist"
        self.assertEqual(plistlib.loads(exported.read_bytes()), {"blob": {"data": b"laptop"}})

    def test_a_dry_run_writes_nothing(self) -> None:
        self.laptop.set_live(DOMAIN, size="14", blob={"data": b"laptop"})
        self.laptop.set_live("com.example.other", theme="light")
        self.laptop.set_login_items("Chrome")
        before = self.laptop.working_copy()
        self.laptop.sync("sync-macos-defaults.py", "--dry-run")
        self.assertEqual(self.laptop.working_copy(), before)


@final
class BrowserTest(SettingsTest):
    def write_chrome(self, mac: Mac, *, saver: bool) -> Path:
        path = mac.home / "Library/Application Support/Google/Chrome/Local State"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hardware_acceleration_mode": {"enabled": saver}}))
        return path

    def read_chrome(self, path: Path) -> object:
        state = cast("dict[str, dict[str, object]]", json.loads(path.read_text()))
        return state["hardware_acceleration_mode"]["enabled"]

    def test_the_owner_writes_the_repo_and_a_follower_does_not(self) -> None:
        self.write_chrome(self.laptop, saver=False)
        self.laptop.sync("sync-browser-local-state.py")
        exported = self.laptop.repo / "scripts/setup/browser-local-state/chrome.json"
        self.assertEqual(json.loads(exported.read_text()), {"hardware_acceleration_mode.enabled": False})

        shutil.copy(exported, self.host.repo / "scripts/setup/browser-local-state/chrome.json")
        before = self.host.working_copy()
        path = self.write_chrome(self.host, saver=True)
        self.host.sync("sync-browser-local-state.py")
        self.assertEqual(self.read_chrome(path), False)

        self.write_chrome(self.host, saver=True)
        self.host.sync("sync-browser-local-state.py")
        self.assertEqual(self.read_chrome(path), True)
        self.assertEqual(self.host.working_copy(), before)

    def test_a_follower_does_not_start_a_browser_file(self) -> None:
        before = self.host.working_copy()
        self.write_chrome(self.host, saver=True)
        self.host.sync("sync-browser-local-state.py")
        self.assertEqual(self.host.working_copy(), before)


if __name__ == "__main__":
    unittest.main()
