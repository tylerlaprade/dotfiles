# Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import final, override

REPOSITORY = Path(__file__).resolve().parents[1]
ROLES = {"agent", "personal"}


def table_lines(table: str) -> list[list[str]]:
    return [line.split() for line in table.splitlines() if line.strip() and not line.startswith("#")]


def packages(name: str) -> list[str]:
    return [line for line in (REPOSITORY / name).read_text().splitlines() if line]


@final
class RoleLookupTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.command = self.root / "scripts/bin/machine-role.sh"
        self.table = self.root / "scripts/setup/machines.conf"
        self.command.parent.mkdir(parents=True)
        self.table.parent.mkdir(parents=True)
        shutil.copy(REPOSITORY / "scripts/bin/machine-role.sh", self.command)
        self.table.write_text("# HostName   role\nLaptop   personal\nHost   agent\n")
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()

    def run_on(self, host_name: str | None, command: Path | None = None) -> subprocess.CompletedProcess[str]:
        answer = "exit 1" if host_name is None else f"echo '{host_name}'"
        scutil = self.fakes / "scutil"
        scutil.write_text(f'#!/bin/bash\n[[ "$*" == "--get HostName" ]] || exit 2\n{answer}\n')
        scutil.chmod(0o755)
        environment = {"PATH": f"{self.fakes}:/usr/bin:/bin", "HOME": os.environ["HOME"]}
        return subprocess.run(
            [str(command or self.command)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def test_a_listed_mac_gets_its_role(self) -> None:
        for host_name, role in (("Laptop", "personal"), ("Host", "agent")):
            with self.subTest(host_name):
                result = self.run_on(host_name)
                self.assertEqual((result.returncode, result.stdout), (0, f"{role}\n"), result.stderr)

    def test_it_runs_through_a_link_on_the_path(self) -> None:
        link = self.root / "machine-role"
        link.symlink_to(self.command)
        self.assertEqual(self.run_on("Host", link).stdout, "agent\n")

    def test_an_unknown_mac_fails_with_the_line_to_add(self) -> None:
        result = self.run_on("New-Mac")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn(str(self.table), result.stderr)
        for role in ROLES:
            self.assertIn(f"  New-Mac   {role}\n", result.stderr)

    def test_a_name_that_only_starts_the_same_is_unknown(self) -> None:
        self.assertEqual(self.run_on("Laptop-2").returncode, 1)

    def test_a_suggested_line_works_once_added(self) -> None:
        suggestion = self.run_on("New-Mac").stderr.splitlines()[-1]
        with self.table.open("a") as table:
            table.write(f"{suggestion}\n")
        self.assertIn(self.run_on("New-Mac").stdout.strip(), ROLES)

    def test_a_mac_without_a_host_name_fails(self) -> None:
        result = self.run_on(None)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("no HostName", result.stderr)

    def test_a_role_nothing_handles_fails(self) -> None:
        self.table.write_text("Host   server\n")
        result = self.run_on("Host")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("server", result.stderr)

    def test_a_mac_listed_twice_fails(self) -> None:
        self.table.write_text("Host   agent\nHost   personal\n")
        result = self.run_on("Host")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("agent, personal", result.stderr)


@final
class RoleTableTest(unittest.TestCase):
    def test_every_line_gives_one_mac_one_role(self) -> None:
        lines = table_lines((REPOSITORY / "scripts/setup/machines.conf").read_text())
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line):
                self.assertEqual(len(line), 2)
                self.assertIn(line[1], ROLES)
        hosts = [line[0] for line in lines]
        self.assertEqual(len(hosts), len(set(hosts)))

    def test_exactly_one_mac_owns_the_settings(self) -> None:
        lines = table_lines((REPOSITORY / "scripts/setup/machines.conf").read_text())
        self.assertEqual([line[1] for line in lines].count("personal"), 1)

    def test_each_role_has_a_brewfile(self) -> None:
        found = {path.name.removeprefix("Brewfile.") for path in REPOSITORY.glob("Brewfile.*")}
        self.assertEqual(found, ROLES)

    def test_no_package_is_both_shared_and_in_a_role(self) -> None:
        shared = packages("Brewfile")
        self.assertEqual(len(shared), len(set(shared)))
        for role in ROLES:
            with self.subTest(role):
                extras = packages(f"Brewfile.{role}")
                self.assertEqual(len(extras), len(set(extras)))
                self.assertEqual(set(shared) & set(extras), set())

    def test_launch_agent_folders_are_roles(self) -> None:
        folders = {path.name for path in (REPOSITORY / "LaunchAgents").iterdir() if path.is_dir()}
        self.assertLessEqual(folders, ROLES)

    def test_no_job_is_both_shared_and_in_a_role(self) -> None:
        names = [path.name for path in (REPOSITORY / "LaunchAgents").rglob("*.plist")]
        self.assertEqual(len(names), len(set(names)))

    def test_the_installer_handles_each_role(self) -> None:
        for script in ("install.sh", "scripts/setup/restore.sh"):
            with self.subTest(script):
                case = re.search(r'^case "\$role" in\n(?P<arms>.*?)^esac$', (REPOSITORY / script).read_text(), re.DOTALL | re.MULTILINE)
                if case is None:
                    self.fail(f"{script} has no case on the role")
                arms = set(re.findall(r"^  (\S+)\)$", case["arms"], re.MULTILINE))
                self.assertEqual(arms, {*ROLES, "*"})


if __name__ == "__main__":
    unittest.main()
