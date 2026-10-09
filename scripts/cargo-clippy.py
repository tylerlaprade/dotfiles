#!/opt/homebrew/bin/python3
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

# rustc lists these unstable lints as warn-by-default (tail-call-track-caller
# since 1.97, malformed-diagnostic-filters since 1.99), then rejects them when
# stable rustc receives them on the command line.
HELP_ONLY_RUSTC_LINTS = {"malformed-diagnostic-filters", "tail-call-track-caller"}

MIN_DEFAULT_RUSTC_WARNINGS = 20

TARGET_SELECTION_FLAGS = {
    "--all-targets",
    "--bench",
    "--benches",
    "--bin",
    "--bins",
    "--example",
    "--examples",
    "--lib",
    "--test",
    "--tests",
}


def rustup_binary(name: str) -> str:
    result = subprocess.run(
        ["rustup", "which", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"rustup could not find {name}")
    return result.stdout.strip()


def rustc_default_warnings(clippy_driver: str) -> list[str]:
    result = subprocess.run(
        [clippy_driver, "-W", "help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "clippy-driver -W help failed")

    warnings: list[str] = []
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

    if len(warnings) < MIN_DEFAULT_RUSTC_WARNINGS:
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


def clippy_args(args: list[str], policy: list[str]) -> list[str]:
    cargo_args = args[: args.index("--")] if "--" in args else args
    selects_targets = any(
        arg.split("=", 1)[0] in TARGET_SELECTION_FLAGS for arg in cargo_args
    )
    every_target = [] if selects_targets else ["--all-targets"]
    if "--" in args:
        split = args.index("--")
        # Personal policy goes last so a caller cannot turn it off by
        # appending an easier lint level to an ordinary cargo command.
        return [*args[:split], *every_target, *args[split:], *policy]
    return [*args, *every_target, "--", *policy]


def main() -> int:
    try:
        real_clippy = rustup_binary("cargo-clippy")
        args = sys.argv[1:]
        cargo_args = args[: args.index("--")] if "--" in args else args
        if any(arg in {"-h", "--help", "-V", "--version"} for arg in cargo_args):
            os.execv(real_clippy, [real_clippy, *args])

        clippy_driver = rustup_binary("clippy-driver")
        policy = policy_args(clippy_driver)
        os.execv(real_clippy, [real_clippy, *clippy_args(args, policy)])
    except (OSError, RuntimeError) as error:
        print(f"cargo-clippy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
