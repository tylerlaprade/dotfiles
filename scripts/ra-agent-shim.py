#!/opt/homebrew/bin/python3
"""Connect an agent's LSP client to the one rust-analyzer lspmux keeps per Cargo workspace.

Agents write straight to disk, so rust-analyzer reads every file from disk: a document a client
holds open is one rust-analyzer stops re-reading, and through lspmux that goes stale for every
session sharing the server. Opening a document or saving one asks for a fresh `cargo check`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, cast
from urllib.parse import unquote, urlparse

type Message = dict[str, object]

DOCUMENT_CONTENT = {"textDocument/didChange", "textDocument/didClose"}
RECHECK_TRIGGERS = {"textDocument/didOpen", "textDocument/didSave"}
RECHECK: Message = {
    "jsonrpc": "2.0",
    "method": "rust-analyzer/runFlycheck",
    "params": {"textDocument": None},
}


def read_message(stream: IO[bytes]) -> Message | None:
    length = 0
    while line := stream.readline():
        if not line.strip():
            return cast("Message", json.loads(stream.read(length)))
        name, _, value = line.decode().partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    return None


def write_message(stream: IO[bytes], message: Message) -> None:
    body = json.dumps(message).encode()
    _ = stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


def workspace_root(directory: Path) -> Path:
    manifest = subprocess.run(
        ["cargo", "locate-project", "--workspace", "--message-format", "plain"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return Path(manifest).parent if manifest else directory


def declared_root(params: Message) -> Path:
    root_uri = params.get("rootUri")
    if isinstance(root_uri, str):
        return Path(unquote(urlparse(root_uri).path))
    return Path(str(params["rootPath"]))


def share_workspace(message: Message) -> Message:
    params = cast("Message", message["params"])
    root = workspace_root(declared_root(params))
    return {
        **message,
        "params": {
            **params,
            "rootPath": str(root),
            "rootUri": root.as_uri(),
            "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
        },
    }


def to_server(message: Message) -> Message | None:
    method = message.get("method")
    if method == "initialize":
        return share_workspace(message)
    if method in DOCUMENT_CONTENT:
        return None
    if method in RECHECK_TRIGGERS:
        return RECHECK
    return message


def forward_client(server: subprocess.Popen[bytes]) -> None:
    server_input = cast("IO[bytes]", server.stdin)
    while (message := read_message(sys.stdin.buffer)) is not None:
        if (forwarded := to_server(message)) is not None:
            write_message(server_input, forwarded)
    server_input.close()


def main() -> int:
    server = subprocess.Popen(
        ["lspmux", "client"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    threading.Thread(target=forward_client, args=(server,), daemon=True).start()
    server_output = cast("IO[bytes]", server.stdout)
    while (message := read_message(server_output)) is not None:
        write_message(sys.stdout.buffer, message)
    return server.wait()


if __name__ == "__main__":
    sys.exit(main())
