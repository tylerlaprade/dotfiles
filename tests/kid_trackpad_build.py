from __future__ import annotations

import importlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast, final
from unittest.mock import patch


class Builder(Protocol):
    def build(self, destination: Path, source: Path | None = None) -> None: ...

    def source_fingerprint(self) -> str: ...


module = importlib.import_module("scripts.kid-trackpad.build")
builder = cast("Builder", cast("object", module))


@final
class BuildTest(unittest.TestCase):
    def test_matching_helper_skips_download_and_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "mouse"
            destination.write_bytes(b"existing executable")
            destination.chmod(0o755)
            destination.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "package_version": "6.2.0",
                        "source_sha256": builder.source_fingerprint(),
                    }
                )
            )
            with (
                patch.object(module, "installed_version", return_value="6.2.0"),
                patch.object(module, "compile_client") as compile_client,
                patch.object(subprocess, "run") as run,
            ):
                builder.build(destination)
            run.assert_not_called()
            compile_client.assert_not_called()

    def test_missing_or_stale_helper_rebuilds_for_installed_driver(self) -> None:
        cases: tuple[dict[str, str] | None, ...] = (
            None,
            {},
            {
                "package_version": "6.1.0",
                "source_sha256": builder.source_fingerprint(),
            },
            {"package_version": "6.2.0", "source_sha256": "old"},
        )
        for metadata in cases:
            with (
                self.subTest(metadata=metadata),
                tempfile.TemporaryDirectory() as temporary,
            ):
                destination = Path(temporary) / "mouse"
                if metadata is not None:
                    destination.write_bytes(b"existing executable")
                    destination.chmod(0o755)
                    destination.with_suffix(".json").write_text(json.dumps(metadata))

                def download(command: list[str], *, check: bool) -> None:
                    self.assertTrue(check)
                    self.assertIn("v6.2.0", command)
                    source = Path(command[-1])
                    source.mkdir()
                    (source / "version.json").write_text(
                        json.dumps({"package_version": "6.2.0"})
                    )

                def compile_client(_source: Path, output: Path) -> None:
                    output.write_bytes(b"new executable")

                with (
                    patch.object(module, "installed_version", return_value="6.2.0"),
                    patch.object(subprocess, "run", side_effect=download),
                    patch.object(module, "compile_client", side_effect=compile_client),
                ):
                    builder.build(destination)
                self.assertEqual(destination.read_bytes(), b"new executable")

    def test_failed_build_preserves_existing_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "driver"
            source.mkdir()
            (source / "version.json").write_text('{"package_version":"6.2.0"}')
            destination = Path(temporary) / "mouse"
            destination.write_bytes(b"existing executable")
            with (
                patch.object(
                    module,
                    "compile_client",
                    side_effect=subprocess.CalledProcessError(1, "clang++"),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                builder.build(destination, source)
            self.assertEqual(destination.read_bytes(), b"existing executable")


if __name__ == "__main__":
    unittest.main()
