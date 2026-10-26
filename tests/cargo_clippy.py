from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

REPOSITORY = Path(__file__).resolve().parents[1]
WRAPPER = REPOSITORY / "scripts" / "cargo-clippy.py"
RUSTC_WARN_LINTS = [f"fake-lint-{index}" for index in range(25)]


class ClippyArgsTest(unittest.TestCase):
    tools: Path = Path()

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tools = Path(directory.name)
        lint_rows = "\n".join(
            f"    {lint}  warn  a fake lint" for lint in RUSTC_WARN_LINTS
        )
        self.write_tool(
            "rustup",
            f'#!/bin/sh\necho "{self.tools}/$2"\n',
        )
        self.write_tool(
            "clippy-driver",
            "#!/bin/sh\ncat <<'EOF'\nLint checks provided by rustc:\n"
            + lint_rows
            + "\nLint groups provided by rustc:\nEOF\n",
        )
        self.write_tool(
            "cargo-clippy",
            "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        )

    def write_tool(self, name: str, source: str) -> None:
        tool = self.tools / name
        _ = tool.write_text(source)
        tool.chmod(0o755)

    def wrapper_args(self, *args: str, ci: bool = False) -> list[str]:
        env = {key: value for key, value in os.environ.items() if key != "CI"} | {
            "PATH": f"{self.tools}:{os.environ['PATH']}"
        }
        if ci:
            env["CI"] = "true"
        result = subprocess.run(
            [str(WRAPPER), *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return result.stdout.splitlines()

    def cargo_args(self, *args: str) -> list[str]:
        arguments = self.wrapper_args(*args)
        return arguments[: arguments.index("--")]

    def lint_args(self, *, ci: bool) -> list[str]:
        arguments = self.wrapper_args("clippy", ci=ci)
        return arguments[arguments.index("--") + 1 :]

    def test_a_plain_run_lints_every_target(self) -> None:
        self.assertEqual(
            self.cargo_args("clippy"), ["clippy", "--all-targets", "--all-features"]
        )

    def test_every_target_goes_before_the_callers_lint_flags(self) -> None:
        self.assertEqual(
            self.cargo_args("clippy", "-p", "castle-game", "--", "-Dwarnings"),
            ["clippy", "-p", "castle-game", "--all-targets", "--all-features"],
        )

    def test_a_caller_who_picks_targets_keeps_them(self) -> None:
        for selection in (
            ["--lib"],
            ["--bin", "qg"],
            ["--test=frozen_bots"],
            ["--examples"],
        ):
            with self.subTest(selection=selection):
                self.assertEqual(
                    self.cargo_args("clippy", *selection),
                    ["clippy", *selection, "--all-features"],
                )

    def test_a_caller_who_picks_features_keeps_them(self) -> None:
        for selection in (
            ["--features", "jev insane"],
            ["--features=jev"],
            ["-Fjev"],
            ["--no-default-features"],
            ["--all-features"],
        ):
            with self.subTest(selection=selection):
                self.assertEqual(
                    self.cargo_args("clippy", *selection),
                    ["clippy", *selection, "--all-targets"],
                )

    def test_a_target_name_after_the_separator_is_not_a_selection(self) -> None:
        self.assertEqual(
            self.cargo_args("clippy", "--", "--lib"),
            ["clippy", "--all-targets", "--all-features"],
        )

    def test_pedantic_warns_locally(self) -> None:
        lints = self.lint_args(ci=False)
        self.assertIn("-Wclippy::pedantic", lints)
        self.assertIn("-Aclippy::too-many-lines", lints)
        self.assertNotIn("-Dclippy::pedantic", lints)

    def test_ci_leaves_pedantic_out(self) -> None:
        local = self.lint_args(ci=False)
        lints = self.lint_args(ci=True)
        self.assertFalse(any("pedantic" in lint for lint in lints))
        self.assertNotIn("-Aclippy::too-many-lines", lints)
        self.assertIn("-Dclippy::all", lints)
        self.assertIn("-Dclippy::todo", lints)
        self.assertEqual(
            [lint for lint in local if lint.startswith("-D")],
            [lint for lint in lints if lint.startswith("-D")],
        )


if __name__ == "__main__":
    _ = unittest.main()
