from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    type Message = dict[str, object]

REPOSITORY = Path(__file__).resolve().parents[1]
SHIM = REPOSITORY / "scripts" / "ra-agent-shim.py"


def load_shim() -> ModuleType:
    loader = SourceFileLoader("ra_agent_shim", str(SHIM))
    spec = spec_from_loader("ra_agent_shim", loader)
    if spec is None:
        raise ImportError(SHIM)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


shim = load_shim()
to_server = cast("Callable[[Message], Message | None]", shim.to_server)
read_message = cast("Callable[[io.BytesIO], Message | None]", shim.read_message)
write_message = cast("Callable[[io.BytesIO, Message], None]", shim.write_message)


def frames(*messages: Message) -> bytes:
    stream = io.BytesIO()
    for message in messages:
        write_message(stream, message)
    return stream.getvalue()


def parse_frames(data: bytes) -> list[Message]:
    stream = io.BytesIO(data)
    messages: list[Message] = []
    while (message := read_message(stream)) is not None:
        messages.append(message)
    return messages


def initialize(root: Path) -> Message:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "rootUri": root.as_uri(),
            "rootPath": str(root),
            "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
            "capabilities": {},
        },
    }


def forwarded(message: Message) -> Message:
    result = to_server(message)
    if result is None:
        raise AssertionError(f"{message['method']} was dropped")
    return result


def notification(method: str, uri: str) -> Message:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": {"textDocument": {"uri": uri}},
    }


class WorkspaceTest(unittest.TestCase):
    workspace: Path = Path()

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve() / "my workspace"
        member = self.workspace / "member" / "src"
        member.mkdir(parents=True)
        _ = (self.workspace / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["member"]\n'
        )
        _ = (self.workspace / "member" / "Cargo.toml").write_text(
            '[package]\nname = "member"\nversion = "0.1.0"\nedition = "2021"\n',
        )
        _ = (member / "lib.rs").write_text("")

    def test_member_session_is_rooted_at_the_workspace(self) -> None:
        params = cast(
            "Message", forwarded(initialize(self.workspace / "member"))["params"]
        )
        self.assertEqual(params["rootPath"], str(self.workspace))
        self.assertEqual(params["rootUri"], self.workspace.as_uri())
        self.assertEqual(
            params["workspaceFolders"],
            [{"uri": self.workspace.as_uri(), "name": "my workspace"}],
        )
        self.assertEqual(params["capabilities"], {})

    def test_directory_outside_any_workspace_keeps_its_root(self) -> None:
        outside = self.workspace.parent / "loose"
        outside.mkdir()
        params = cast("Message", forwarded(initialize(outside))["params"])
        self.assertEqual(params["rootPath"], str(outside))


class DocumentSyncTest(unittest.TestCase):
    uri: str = "file:///workspace/src/lib.rs"

    def test_content_never_reaches_the_server(self) -> None:
        self.assertIsNone(to_server(notification("textDocument/didChange", self.uri)))
        self.assertIsNone(to_server(notification("textDocument/didClose", self.uri)))

    def test_open_and_save_ask_for_a_fresh_check(self) -> None:
        for method in ("textDocument/didOpen", "textDocument/didSave"):
            recheck = forwarded(notification(method, self.uri))
            self.assertEqual(recheck["method"], "rust-analyzer/runFlycheck")
            self.assertEqual(recheck["params"], {"textDocument": None})

    def test_requests_pass_through_unchanged(self) -> None:
        hover: Message = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "textDocument/hover",
            "params": {},
        }
        self.assertEqual(to_server(hover), hover)


class ProcessTest(unittest.TestCase):
    def test_relays_both_directions_through_lspmux_client(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tools = Path(directory.name)
        received = tools / "received"
        greeting = frames(
            {
                "jsonrpc": "2.0",
                "method": "window/logMessage",
                "params": {"message": "hi"},
            }
        )
        lspmux = tools / "lspmux"
        _ = lspmux.write_text(
            f"#!/bin/sh\n[ \"$1\" = client ] || exit 64\nprintf '%s' '{greeting.decode()}'\ncat > '{received}'\n",
        )
        lspmux.chmod(0o755)
        uri = "file:///workspace/src/lib.rs"
        client_input = frames(
            notification("textDocument/didOpen", uri),
            notification("textDocument/didChange", uri),
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
        )
        result = subprocess.run(
            [str(SHIM)],
            input=client_input,
            capture_output=True,
            env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_frames(result.stdout), parse_frames(greeting))
        self.assertEqual(
            [message.get("method") for message in parse_frames(received.read_bytes())],
            ["rust-analyzer/runFlycheck", "shutdown"],
        )
        self.assertEqual(parse_frames(received.read_bytes())[1]["id"], 3)


if __name__ == "__main__":
    _ = unittest.main()
