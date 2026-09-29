# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent as tests see it: an HTTP server on a unix socket that answers its routes the way it does.

`FakeWda` serves WebDriverAgent's API, and its `opener` stands where the cable does
(`connectors.iphone.wda_client.usbmux_opener`). What it answers is WebDriver's JSON; a test can make any route fail as
WebDriverAgent fails, and read every request it was sent. `wda_archive` makes a release's archive, or one that is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any

from sim_mirror.connectors.iphone.wda_client import HTTP_PORT, Opener, Streams
from sim_mirror.connectors.iphone.wda_source import MJPEG_ON_LOOPBACK, PROJECT

#: A small element tree as WebDriverAgent's ``/source?format=json`` answers one: an app with a title, a field and a
#: button, and a view that says nothing.
SOURCE = {
    "type": "Application",
    "name": "Notes",
    "label": "Notes",
    "value": None,
    "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
    "isEnabled": "1",
    "isVisible": "1",
    "children": [
        {
            "type": "Other",
            "name": None,
            "label": None,
            "value": None,
            "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
            "isEnabled": "1",
            "isVisible": "1",
            "children": [
                {
                    "type": "StaticText",
                    "name": "Notes",
                    "label": "Notes",
                    "value": "Notes",
                    "rect": {"x": 16, "y": 96, "width": 120, "height": 41},
                    "isEnabled": "1",
                    "isVisible": "1",
                },
                {
                    "type": "TextField",
                    "name": "title",
                    "rawIdentifier": "title",
                    "label": "Title",
                    "value": "Groceries",
                    "rect": {"x": 16, "y": 160, "width": 361, "height": 44},
                    "isEnabled": "1",
                    "isVisible": "1",
                },
                {
                    "type": "Button",
                    "name": "Save",
                    "label": "Save",
                    "value": None,
                    "rect": {"x": 300, "y": 780, "width": 77, "height": 44},
                    "isEnabled": "0",
                    "isVisible": "1",
                },
                {
                    "type": "Button",
                    "name": "Hidden",
                    "label": "Hidden",
                    "value": None,
                    "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "isEnabled": "1",
                    "isVisible": "0",
                },
            ],
        }
    ],
}

_SESSION = re.compile(r"\A/session/(?P<id>[^/]+)(?P<rest>/.*)?\Z")


class FakeWda:
    """WebDriverAgent on one device, answering on unix sockets beside `folder`."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        #: Every request: its method, path, and JSON body (None when it had none).
        self.requests: list[tuple[str, str, Any]] = []
        #: How many sessions were made.
        self.sessions = 0
        #: The session WebDriverAgent knows now; None when it has none (as after a restart).
        self.session: str | None = None
        self.source: dict[str, Any] = SOURCE
        self.orientation = "PORTRAIT"
        #: What a route answers instead, by method and path with the session left out: an HTTP status and a body.
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}
        self._servers: list[asyncio.AbstractServer] = []

    def path(self, port: int) -> Path:
        return self.folder / f"wda-{port}.sock"

    async def serve(self) -> FakeWda:
        self._servers = [await asyncio.start_unix_server(self._http, path=str(self.path(HTTP_PORT)))]
        return self

    async def stop(self) -> None:
        for server in self._servers:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        self._servers = []

    def opener(self) -> Opener:
        async def open_port(port: int) -> Streams:
            return await asyncio.open_unix_connection(str(self.path(port)))

        return open_port

    def fail(self, method: str, path: str, error: str, message: str, status: int = 500) -> None:
        """Make a route answer a WebDriver error, as WebDriverAgent does."""
        self.answers[(method, path)] = (status, {"value": {"error": error, "message": message}})

    def calls(self, path: str) -> list[Any]:
        """The bodies of the requests to a path, with the session left out."""
        return [body for _method, seen, body in self.requests if _unsessioned(seen) == path]

    async def _http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, _version = (await reader.readline()).decode().split(" ", 2)
            length = 0
            while (line := await reader.readline()) not in (b"\r\n", b""):
                name, _, value = line.decode().partition(":")
                if name.lower() == "content-length":
                    length = int(value)
            raw = await reader.readexactly(length) if length else b""
            body = json.loads(raw) if raw else None
            self.requests.append((method, target, body))
            status, document = self._answer(method, target, body)
            payload = document if isinstance(document, bytes) else json.dumps(document).encode()
            reason = "OK" if status < 400 else "Error"
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()

    def _answer(self, method: str, target: str, body: Any) -> tuple[int, Any]:
        path, _, query = target.partition("?")
        match = _SESSION.match(path)
        if match is not None and match["id"] != self.session:
            return 404, {"value": {"error": "invalid session id", "message": f"Session does not exist: {match['id']}"}}
        plain = _unsessioned(path)
        if (method, plain) in self.answers:
            return self.answers[(method, plain)]
        if (method, plain) == ("POST", "/session"):
            self.sessions += 1
            self.session = f"S{self.sessions}"
            return 200, {"sessionId": self.session, "value": {"sessionId": self.session, "capabilities": {}}}
        if plain == "/status":
            return 200, {"value": {"ready": True, "build": {"version": "16.12.10"}}}
        if plain == "/source" and "format=json" in query:
            return 200, {"value": self.source}
        if plain == "/orientation":
            return 200, {"value": self.orientation}
        if plain == "/element/active":
            return 200, {"value": {"ELEMENT": "E1", "element-6066-11e4-a52e-4f735466cecf": "E1"}}
        return 200, {"value": None}


def _unsessioned(path: str) -> str:
    path = path.partition("?")[0]
    match = _SESSION.match(path)
    return match["rest"] or "/" if match is not None else path


#: A release's commit and the folder its archive unpacks into, as tests make them.
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FOLDER = f"WebDriverAgent-{COMMIT}"
#: WebDriverAgent's FBWebServer.m, as far as SimMirror's change to it reads it.
SERVER = (
    "  self.screenshotsBroadcaster = [[FBTCPSocket alloc]\n"
    + MJPEG_ON_LOOPBACK.old
    + "  self.mjpegServer.socket = x;\n"
).encode()


def wda_archive(*entries: tuple[str, bytes | None], link: str | None = None, script: bool = False) -> bytes:
    """A gzipped tar of folders (None) and files -- a script that runs, when asked -- and a symbolic link: a
    release's archive, or one that is not. With no entries, the smallest archive SimMirror builds from."""
    entries = entries or (
        (FOLDER, None),
        (f"{FOLDER}/{PROJECT}", None),
        (f"{FOLDER}/{MJPEG_ON_LOOPBACK.path}", SERVER),
    )
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o755 if script and name.endswith(".sh") else 0o644
                tar.addfile(info, io.BytesIO(data))
        if link is not None:
            info = tarfile.TarInfo(f"{FOLDER}/escape")
            info.type, info.linkname = tarfile.SYMTYPE, link
            tar.addfile(info)
    return out.getvalue()
