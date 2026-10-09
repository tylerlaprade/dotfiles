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

    def cargo_args(self, *args: str) -> list[str]:
        result = subprocess.run(
            [str(WRAPPER), *args],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PATH": f"{self.tools}:{os.environ['PATH']}"},
        )
        arguments = result.stdout.splitlines()
        return arguments[: arguments.index("--")]

    def test_a_plain_run_lints_every_target(self) -> None:
        self.assertEqual(self.cargo_args("clippy"), ["clippy", "--all-targets"])

    def test_every_target_goes_before_the_callers_lint_flags(self) -> None:
        self.assertEqual(
            self.cargo_args("clippy", "-p", "castle-game", "--", "-Dwarnings"),
            ["clippy", "-p", "castle-game", "--all-targets"],
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
                    self.cargo_args("clippy", *selection), ["clippy", *selection]
                )

    def test_a_target_name_after_the_separator_is_not_a_selection(self) -> None:
        self.assertEqual(
            self.cargo_args("clippy", "--", "--lib"), ["clippy", "--all-targets"]
        )


if __name__ == "__main__":
    _ = unittest.main()
