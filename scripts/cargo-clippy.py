#!/usr/bin/env python3
"""Apply one personal lint policy to every local `cargo clippy` run."""

import os
import re
import subprocess
import sys


PEDANTIC_WHITELIST = {
    "cast-possible-truncation",
    "cast-possible-wrap",
    "cast-precision-loss",
    "cast-sign-loss",
    "missing-errors-doc",
    "missing-panics-doc",
    "similar-names",
    "struct-excessive-bools",
    "too-many-lines",
}

# These restriction lints guard defects with little noise. Do not enable the
# whole restriction group: its members conflict with each other by design.
EXTRA_DENY = {
    "allow-attributes",
    "allow-attributes-without-reason",
    "dbg-macro",
    "mem-forget",
    "precedence-bits",
    "renamed-function-params",
    "todo",
}

# rustc 1.97 lists this unstable lint as warn-by-default, then rejects it when
# stable rustc receives it on the command line.
HELP_ONLY_RUSTC_LINTS = {"tail-call-track-caller"}


def rustup_binary(name: str) -> str:
    result = subprocess.run(
        ["rustup", "which", name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"rustup could not find {name}")
    return result.stdout.strip()


def rustc_default_warnings(clippy_driver: str) -> list[str]:
    result = subprocess.run(
        [clippy_driver, "-W", "help"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "clippy-driver -W help failed")

    warnings = []
    in_rustc_lints = False
    for line in result.stdout.splitlines():
        if line == "Lint checks provided by rustc:":
            in_rustc_lints = True
            continue
        if line == "Lint groups provided by rustc:":
            break
        if not in_rustc_lints:
            continue
        match = re.match(r"^\s+([a-z0-9-]+)\s+warn\s+", line)
        if (
            match
            and match.group(1) != "warnings"
            and match.group(1) not in HELP_ONLY_RUSTC_LINTS
        ):
            warnings.append(match.group(1))

    if len(warnings) < 20:
        raise RuntimeError(
            f"found only {len(warnings)} default rustc warnings; refusing to weaken policy"
        )
    return warnings


def policy_args(clippy_driver: str) -> list[str]:
    # Deny rustc's default warnings one by one instead of using -Dwarnings.
    # That leaves pedantic at warn and lets #[expect(..., reason = "...")]
    # suppress a false positive. --force-warn would defeat those expectations.
    args = ["-Dclippy::all"]
    args.extend(f"-D{lint}" for lint in rustc_default_warnings(clippy_driver))
    args.extend(f"-Dclippy::{lint}" for lint in sorted(EXTRA_DENY))
    args.append("-Wclippy::pedantic")
    args.extend(f"-Aclippy::{lint}" for lint in sorted(PEDANTIC_WHITELIST))
    return args


def main() -> int:
    try:
        real_clippy = rustup_binary("cargo-clippy")
        args = sys.argv[1:]
        cargo_args = args[: args.index("--")] if "--" in args else args
        if any(arg in {"-h", "--help", "-V", "--version"} for arg in cargo_args):
            os.execv(real_clippy, [real_clippy, *args])

        clippy_driver = rustup_binary("clippy-driver")
        policy = policy_args(clippy_driver)
        if "--" in args:
            # Personal policy goes last so a caller cannot turn it off by
            # appending an easier lint level to an ordinary cargo command.
            args.extend(policy)
        else:
            args.extend(["--", *policy])
        os.execv(real_clippy, [real_clippy, *args])
    except (OSError, RuntimeError) as error:
        print(f"cargo-clippy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
