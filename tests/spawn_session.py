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
        for name in ("ghostty-tab", "rex-path", "spawn-session"):
            (self.bin / name).symlink_to(REPOSITORY / f"scripts/bin/{name}.sh")
        self.install(
            self.bin / "osascript",
            'case "$*" in *com.superlogical.rex*) cat "$CALLS.rex-app" 2>/dev/null; exit 0 ;; esac\n'
            'cat > "$CALLS.script"\nprintf "%s\\n" "$@" > "$CALLS.osascript"\necho tab-7\n',
        )
        self.install(
            self.bin / "claude",
            'printf "%s\\n" "$@" > "$CALLS.claude"\nprintf "Starting background service…\\nbackgrounded · 4f2a\\n  claude attach 4f2a    open in this terminal\\n"\n',
        )
        self.lockout(0)

    def install(self, path: Path, body: str) -> None:
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def lockout(self, status: int) -> None:
        self.install(
            self.home / ".cargo/bin/session-guard",
            f'printf "%s\\n" "$*" >> "$CALLS.guard"\n[ "$1 ${{2:-}}" = "lockout " ] && exit {status}\nexit 0\n',
        )

    def install_rex(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.install(
            path,
            'printf "%s|%s\\n" "${REX_SESSION:-}" "$*" >> "$CALLS.rex"\n'
            'if [ "$1" = new ]; then cat "$CALLS.rex-reply"; exit "$(cat "$CALLS.rex-status")"; fi\n',
        )
        Path(f"{self.calls}.rex-reply").write_text(
            '{"session_id":"session:1","initial_windows":[{"block_ids":["block:1"]}]}\n'
        )
        Path(f"{self.calls}.rex-status").write_text("0\n")

    def run_command(
        self, *arguments: str, **extra_environment: str
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CALLS": str(self.calls),
            **extra_environment,
        }
        return subprocess.run(
            arguments,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def recorded(self, kind: str) -> str:
        return Path(f"{self.calls}.{kind}").read_text()

    def test_tab_types_the_command_and_keeps_the_previous_tab_selected(self) -> None:
        result = self.run_command("ghostty-tab", str(self.folder), "make test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tab-7\n")
        arguments = self.recorded("osascript").splitlines()
        self.assertEqual(arguments[1:3], [str(self.folder), "make test"])
        script = self.recorded("script")
        self.assertIn(
            'if frontAppId is not "" then tell application id frontAppId to activate',
            script,
        )
        self.assertIn(
            "activate window (first window whose id is previousWindowId)", script
        )
        self.assertIn("set previousId to id of selected tab of mainWindow", script)
        self.assertIn(
            "if selected of t then select tab (first tab of mainWindow whose id is previousId)",
            script,
        )
        self.assertIn("lockout --wait", self.recorded("guard"))

    def test_session_is_typed_into_a_tab_with_its_options_and_brief(self) -> None:
        result = self.run_command(
            "spawn-session", str(self.folder), str(self.brief), "--name", "Kid tabs"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tab-7\n")
        typed = self.recorded("osascript").splitlines()[2]
        self.assertEqual(
            typed,
            f'claude --name Kid\\ tabs "$(cat {str(self.brief).replace(" ", "\\ ")})"',
        )
        self.assertFalse(Path(f"{self.calls}.claude").exists())

    def test_session_starts_in_the_background_during_a_lockout(self) -> None:
        self.lockout(LOCKED_OUT)
        result = self.run_command(
            "spawn-session", str(self.folder), str(self.brief), "--name", "Kid tabs"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "background 4f2a\n")
        self.assertEqual(
            self.recorded("claude").splitlines(),
            ["--bg", "--name", "Kid tabs", "Do the thing."],
        )
        self.assertIn("attach-after-lockout 4f2a", self.recorded("guard"))
        self.assertFalse(Path(f"{self.calls}.osascript").exists())

    def rex_calls(self) -> list[str]:
        return self.recorded("rex").splitlines()

    def assert_no_ghostty(self) -> None:
        self.assertFalse(Path(f"{self.calls}.osascript").exists())
        self.assertFalse(Path(f"{self.calls}.guard").exists())

    def test_session_starts_in_a_rex_terminal_with_rex_on_path(self) -> None:
        self.install_rex(self.bin / "rex")
        result = self.run_command(
            "spawn-session", str(self.folder), str(self.brief), "--name", "Kid tabs"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "block:1\n")
        new, *typed = self.recorded("rex").split("\n", 1)
        self.assertEqual(new, f"|new Kid tabs -c {self.folder} --json")
        self.assertEqual(
            typed[0],
            f'|send -b block:1 claude --name Kid\\ tabs "$(cat {str(self.brief).replace(" ", "\\ ")})"\n\n',
        )
        self.assert_no_ghostty()

    def test_rex_is_found_inside_its_app_when_it_is_not_on_path(self) -> None:
        app = self.root / "Any Name.app"
        self.install_rex(app / "Contents/Helpers/Any.rex/Contents/MacOS/rex")
        Path(f"{self.calls}.rex-app").write_text(f"{app}\n")
        result = self.run_command("spawn-session", str(self.folder), str(self.brief))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "block:1\n")
        self.assertIn("|new work -c ", self.rex_calls()[0])
        self.assert_no_ghostty()

    def test_a_rex_terminal_can_start_a_session_outside_its_own_session(self) -> None:
        self.install_rex(self.bin / "rex")
        result = self.run_command(
            "spawn-session",
            str(self.folder),
            str(self.brief),
            REX_SESSION="session:mine",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rex_calls()[0].startswith("|new "))
        self.assertTrue(any(call.startswith("|send ") for call in self.rex_calls()))

    def test_a_rex_label_never_reads_as_a_flag(self) -> None:
        self.install_rex(self.bin / "rex")
        dashed = self.root / "--work"
        dashed.mkdir()
        result = self.run_command("spawn-session", str(dashed), str(self.brief))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("|new work -c ", self.rex_calls()[0])
        only_dashes = self.root / "--"
        only_dashes.mkdir()
        result = self.run_command("spawn-session", str(only_dashes), str(self.brief))
        self.assertEqual(result.returncode, 0, result.stderr)
        started = [call for call in self.rex_calls() if call.startswith("|new ")]
        self.assertIn("|new -c ", started[-1])

    def test_a_failed_rex_session_never_falls_back_to_ghostty(self) -> None:
        self.install_rex(self.bin / "rex")
        Path(f"{self.calls}.rex-status").write_text("3\n")
        result = self.run_command("spawn-session", str(self.folder), str(self.brief))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assert_no_ghostty()

    def test_missing_folder_fails_before_anything_starts(self) -> None:
        self.install_rex(self.bin / "rex")
        result = self.run_command(
            "spawn-session", str(self.root / "missing"), str(self.brief)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no folder", result.stderr)
        self.assertFalse(Path(f"{self.calls}.rex").exists())

    def test_unreadable_brief_fails_before_anything_starts(self) -> None:
        result = self.run_command(
            "spawn-session", str(self.folder), str(self.root / "missing.md")
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read", result.stderr)
        self.assertFalse(Path(f"{self.calls}.osascript").exists())

    def test_background_owner_checks_permission_before_opening_a_tab(self) -> None:
        owner = self.bin / "owner"
        self.install(owner, 'printf "%s\\n" "$@" > "$CALLS.owner"\nexit 1\n')
        result = self.run_command(
            "ghostty-tab", str(self.folder), "make test", str(owner)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded("owner"), "check\n")
        self.assertIn("lockout --wait", self.recorded("guard"))
        self.assertFalse(Path(f"{self.calls}.osascript").exists())


if __name__ == "__main__":
    unittest.main()
