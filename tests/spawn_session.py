# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import final, override

REPOSITORY = Path(__file__).resolve().parents[1]
LOCKED_OUT = 1


@final
class SpawnSessionTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        (self.home / ".cargo/bin").mkdir(parents=True)
        self.calls = self.root / "calls"
        self.folder = self.root / "work"
        self.folder.mkdir()
        self.brief = self.root / "my brief.md"
        self.brief.write_text("Do the thing.\n")
        for name in ("ghostty-tab", "spawn-session"):
            (self.bin / name).symlink_to(REPOSITORY / f"scripts/bin/{name}.sh")
        self.install(self.bin / "osascript", 'cat > "$CALLS.script"\nprintf "%s\\n" "$@" > "$CALLS.osascript"\necho tab-7\n')
        self.install(self.bin / "claude", 'printf "%s\\n" "$@" > "$CALLS.claude"\necho "backgrounded · 4f2a · use claude attach"\n')
        self.lockout(0)

    def install(self, path: Path, body: str) -> None:
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def lockout(self, status: int) -> None:
        self.install(
            self.home / ".cargo/bin/session-guard",
            f'printf "%s\\n" "$*" >> "$CALLS.guard"\n[ "$1 ${{2:-}}" = "lockout " ] && exit {status}\nexit 0\n',
        )

    def run_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.calls)}
        return subprocess.run(arguments, env=environment, text=True, capture_output=True, timeout=10, check=False)

    def recorded(self, kind: str) -> str:
        return Path(f"{self.calls}.{kind}").read_text()

    def test_tab_types_the_command_and_keeps_the_previous_tab_selected(self) -> None:
        result = self.run_command("ghostty-tab", str(self.folder), "make test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tab-7\n")
        arguments = self.recorded("osascript").splitlines()
        self.assertEqual(arguments[1:3], [str(self.folder), "make test"])
        script = self.recorded("script")
        self.assertIn("set previousId to id of selected tab of mainWindow", script)
        self.assertIn("if selected of t then select tab (first tab of mainWindow whose id is previousId)", script)
        self.assertIn("activate window (window id previousFrontId)", script)
        self.assertIn("lockout --wait", self.recorded("guard"))

    def test_session_is_typed_into_a_tab_with_its_options_and_brief(self) -> None:
        result = self.run_command("spawn-session", str(self.folder), str(self.brief), "--name", "Kid tabs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tab-7\n")
        typed = self.recorded("osascript").splitlines()[2]
        self.assertEqual(typed, f'claude --name Kid\\ tabs "$(cat {str(self.brief).replace(" ", "\\ ")})"')
        self.assertFalse(Path(f"{self.calls}.claude").exists())

    def test_session_starts_in_the_background_during_a_lockout(self) -> None:
        self.lockout(LOCKED_OUT)
        result = self.run_command("spawn-session", str(self.folder), str(self.brief), "--name", "Kid tabs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "background 4f2a\n")
        self.assertEqual(self.recorded("claude").splitlines(), ["--bg", "--name", "Kid tabs", "Do the thing."])
        self.assertIn("attach-after-lockout 4f2a", self.recorded("guard"))
        self.assertFalse(Path(f"{self.calls}.osascript").exists())

    def test_unreadable_brief_fails_before_anything_starts(self) -> None:
        result = self.run_command("spawn-session", str(self.folder), str(self.root / "missing.md"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read", result.stderr)
        self.assertFalse(Path(f"{self.calls}.osascript").exists())


if __name__ == "__main__":
    unittest.main()
