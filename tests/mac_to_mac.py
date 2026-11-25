# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
"""Runs pre-wipe.sh and restore.sh between two throwaway home folders."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import final, override

REPOSITORY = Path(__file__).resolve().parents[1]
PROFILES = "Library/Developer/Xcode/UserData/Provisioning Profiles"
CARRIED = {
    "Keys/AuthKey_TEST.p8": "app store key",
    f"{PROFILES}/app.mobileprovision": "profile",
    "Pictures/Fondly/photos/one photo.heic": "photo",
    "Pictures/Fondly/preview-take.mov": "take",
    ".ssh/id_ed25519": "ssh key",
    ".gnupg/pubring.kbx": "public keys",
    ".gnupg/private-keys-v1.d/one.key": "private key",
    ".config/gh/hosts.yml": "github",
    ".config/sourcery/auth.yaml": "sourcery",
    ".netrc": "netrc",
    ".zshenv.local": "shell",
    ".zsh_history": "history",
    "Documents/letter.txt": "letter",
    "Desktop/note.txt": "note",
    "Library/Fonts/Font.ttf": "font",
}
LEFT_BEHIND = (
    ".gnupg/public-keys.d/pubring.db.lock",
    ".gnupg/public-keys.d/.#lk0x1.Old-Mac.1",
    "Documents/Zoom/call.mp4",
    "Documents/Screen Recording 1.mov",
)


@final
class MacToMacTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.fakes = self.root / "fakes"
        self.setup = self.root / "dotfiles/scripts/setup"
        for folder in (self.old, self.new, self.fakes, self.setup, self.root / "dotfiles/scripts/bin"):
            folder.mkdir(parents=True)
        for name in ("Documents", "Desktop"):
            (self.new / name).mkdir()
        for name in ("pre-wipe.sh", "restore.sh"):
            shutil.copy(REPOSITORY / "scripts/setup" / name, self.setup / name)
        shutil.copy(REPOSITORY / "scripts/bin/machine-role.sh", self.root / "dotfiles/scripts/bin/machine-role.sh")
        (self.setup / "machines.conf").write_text("Host agent\n")
        subprocess.run(["git", "init", "-q", str(self.root / "dotfiles")], check=True)
        self.fake("scutil", "echo Host")
        self.fake(
            "ssh",
            "login=''\n"
            'while [[ $1 == -* ]]; do [[ $1 == -l ]] && login="$2@"; [[ $1 == -[oOl] ]] && shift; shift; done\n'
            '[[ $login$1 == tyler@new ]] || { echo "ssh: no host $login$1" >&2; exit 255; }\n'
            "shift\n[[ $# -eq 0 ]] && exit 0\n"
            f'cd "{self.new}" && HOME="{self.new}" exec /bin/bash -c "$*"',
        )
        for path, body in CARRIED.items():
            self.write(self.old / path, body)
        for path in LEFT_BEHIND:
            self.write(self.old / path, "left behind")
        (self.old / ".ssh/id_ed25519").chmod(0o600)
        self.write(self.old / f".claude/projects/{self.slug(self.old)}-Code-app/memory/MEMORY.md", "memory")

    def fake(self, name: str, body: str) -> None:
        path = self.fakes / name
        path.write_text(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)

    def write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    def slug(self, home: Path) -> str:
        return str(home).replace("/", "-")

    def run_script(self, home: Path, name: str, *arguments: str, answers: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.setup / name), *arguments],
            capture_output=True,
            text=True,
            check=False,
            input="".join(f"{answer}\n" for answer in answers),
            env={"PATH": f"{self.fakes}:/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(home)},
        )

    def copy(self) -> None:
        result = self.run_script(self.old, "pre-wipe.sh", "tyler@new", answers="yy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_everything_lands_where_it_was(self) -> None:
        self.copy()
        result = self.run_script(self.new, "restore.sh", answers="y")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path, body in CARRIED.items():
            with self.subTest(path):
                self.assertEqual((self.new / path).read_text(), body)
        for path in LEFT_BEHIND:
            with self.subTest(path):
                self.assertFalse((self.new / path).exists())
        memory = self.new / f".claude/projects/{self.slug(self.new)}-Code-app/memory/MEMORY.md"
        self.assertEqual(memory.read_text(), "memory")
        self.assertEqual((self.new / ".ssh").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.new / ".ssh/id_ed25519").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.new / "machine-backup").exists())

    def test_the_copy_on_the_new_mac_is_private(self) -> None:
        self.copy()
        self.assertEqual((self.new / "machine-backup").stat().st_mode & 0o777, 0o700)

    def test_the_old_mac_keeps_everything_and_gains_nothing(self) -> None:
        before = {path: path.read_bytes() for path in self.old.rglob("*") if path.is_file()}
        self.copy()
        after = {path: path.read_bytes() for path in self.old.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_a_second_run_finishes_an_interrupted_one(self) -> None:
        self.copy()
        (self.new / "machine-backup/Keys/AuthKey_TEST.p8").unlink()
        self.copy()
        self.assertTrue((self.new / "machine-backup/Keys/AuthKey_TEST.p8").exists())

    def test_the_signing_export_is_carried(self) -> None:
        export = self.old / "Desktop/machine-backup/xcode.developerprofile"
        self.fake("chmod", f'[[ "$*" == *machine-backup* ]] && mkdir -p "{export.parent}"; /bin/chmod "$@"')
        self.fake("mkdir", f'/bin/mkdir "$@"; [[ -d "{export.parent}" ]] && echo signing > "{export}"; exit 0')
        self.copy()
        self.assertEqual((self.new / "machine-backup/xcode.developerprofile").read_text(), "signing\n")
        result = self.run_script(self.new, "restore.sh", answers="n")
        self.assertIn("machine-backup/xcode.developerprofile", result.stdout)
        self.assertTrue((self.new / "machine-backup/xcode.developerprofile").exists())

    def test_the_archive_folder_restores_too(self) -> None:
        self.copy()
        shutil.move(self.new / "machine-backup", self.root / "unpacked")
        result = self.run_script(self.new, "restore.sh", str(self.root / "unpacked"), answers="n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.new / "Keys/AuthKey_TEST.p8").read_text(), "app store key")

    def test_a_mac_that_cannot_be_reached_stops_before_copying(self) -> None:
        result = self.run_script(self.old, "pre-wipe.sh", "tyler@elsewhere", answers="yy")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Remote Login", result.stdout)
        self.assertFalse((self.old / "Desktop/machine-backup").exists())

    def test_declining_the_signing_export_stops(self) -> None:
        result = self.run_script(self.old, "pre-wipe.sh", "tyler@new", answers="yn")
        self.assertEqual(result.returncode, 1)

    def test_restoring_nothing_explains_itself(self) -> None:
        result = self.run_script(self.new, "restore.sh", answers="n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage: restore.sh", result.stdout)

    def test_restoring_on_a_mac_with_no_role_stops_first(self) -> None:
        self.copy()
        (self.setup / "machines.conf").write_text("Other agent\n")
        result = self.run_script(self.new, "restore.sh", answers="n")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.new / "Keys").exists())


if __name__ == "__main__":
    unittest.main()
