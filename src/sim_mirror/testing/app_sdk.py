# SPDX-License-Identifier: Apache-2.0
"""A fake app built with SimMirror's debug SDK: a real HTTP server on 127.0.0.1 answering as the app SDK protocol says.

`FakeAppSdk` listens on a port of its own and answers ``GET /v1/hierarchy`` with `document` for a request carrying
its secret, as an app in front does -- or as `answer` says an app misbehaves: not in front, refusing, hanging, hanging
up, or answering too much or in the wrong shape. `requests` is what it was asked. `listing` is the listing such an
app writes, and `write_listing` puts one where an app would, under a simulator's data folder or an app's container.

`app_node` and `app_hierarchy` make the shapes ``protocol/app-sdk/v1`` defines, every property sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from sim_mirror.connectors.app import wire

BUNDLE_ID = "io.github.andrewkochulab.simmirror.appsdk"
SECRET = "q2Vn1c7yJx0mH4tR8bW3sK6pZ9dL5fA2gE7uY1oN3iC"
#: The ways a fake app can answer, besides with its hierarchy.
ANSWERS = frozenset(
    {"hierarchy", "inactive", "refuse", "hang", "hang_up", "not_json", "not_object", "too_large", "chunked",
     "unsized", "unsized_too_large", "long_headers", "not_http", "bad_length"}
)  # fmt: skip


def app_node(
    kind: str = "button",
    label: str | None = "Go",
    frame: tuple[float, float, float, float] = (0, 0, 44, 44),
    *,
    children: Sequence[dict[str, Any]] = (),
    **more: Any,
) -> dict[str, Any]:
    """A node as an app sends it: `more` sets any other property."""
    x, y, width, height = frame
    node: dict[str, Any] = {
        "kind": kind, "label": label, "label_source": "text" if label else None, "identifier": None, "value": None,
        "placeholder": None, "frame": {"x": x, "y": y, "width": width, "height": height}, "traits": [],
        "enabled": True, "interactive": kind == "button", "source": "uikit", "type_name": "UIView",
        "children": list(children),
    }  # fmt: skip
    node.update(more)
    return node


def app_hierarchy(
    *nodes: dict[str, Any], bundle_id: str = BUNDLE_ID, pid: int | None = None, name: str = "AppSDK", **more: Any
) -> dict[str, Any]:
    """A hierarchy as an app in front sends it, its nodes in one key window: `more` sets any other property."""

    def count(items: Sequence[dict[str, Any]]) -> int:
        return sum(1 + count(item["children"]) for item in items)

    document: dict[str, Any] = {
        "protocol": wire.PROTOCOL_VERSION, "sdk_version": "1.0.0",
        "app": {"bundle_id": bundle_id, "name": name, "pid": os.getpid() if pid is None else pid, "active": True},
        "screen": {"width_pt": 402, "height_pt": 874, "scale": 3, "orientation": "portrait"},
        "modal": None, "keyboard": None, "windows": [{"level": 0, "key": True, "nodes": list(nodes)}],
        "truncated": False, "node_count": count(nodes), "capture_ms": 1.0, "notes": [],
    }  # fmt: skip
    document.update(more)
    return document


def write_listing(folder: Path, listing: dict[str, Any], *, mode: int = 0o600) -> Path:
    """Write a listing into `folder` -- a simulator's data folder, or an app's container -- where an app would."""
    apps = folder.joinpath(*wire.LISTINGS)
    apps.mkdir(parents=True, exist_ok=True)
    path = apps / f"{listing['bundle_id']}.json"
    path.write_text(json.dumps(listing))
    path.chmod(mode)
    return path


@dataclass(frozen=True)
class AppRequest:
    """A request the fake app was sent: its request line and its headers, names in lower case."""

    line: str
    headers: dict[str, str]


class FakeAppSdk:
    """An app in front sharing its view hierarchy, or misbehaving as `answer` says (one of `ANSWERS`)."""

    def __init__(
        self,
        document: dict[str, Any] | None = None,
        *,
        bundle_id: str = BUNDLE_ID,
        name: str = "AppSDK",
        pid: int | None = None,
        secret: str = SECRET,
    ) -> None:
        self.bundle_id = bundle_id
        self.name = name
        self.pid = os.getpid() if pid is None else pid
        self.secret = secret
        self.document = document if document is not None else app_hierarchy(app_node(), bundle_id=bundle_id)
        self.answer = "hierarchy"
        self.requests: list[AppRequest] = []
        self._server: asyncio.Server | None = None
        self._port: int | None = None
        self._closing = asyncio.Event()

    @property
    def port(self) -> int:
        """The port the app listens on -- and, once it has closed, listened on."""
        assert self._port is not None, "start the fake app first"
        return self._port

    async def start(self) -> FakeAppSdk:
        self._server = await asyncio.start_server(self._serve, wire.HOST, 0)
        self._port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        self._closing.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def __aenter__(self) -> FakeAppSdk:
        return await self.start()

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        await self.close()

    def listing(self, udid: str, **more: Any) -> dict[str, Any]:
        """The listing this app writes on the simulator `udid`: `more` sets any other property."""
        listing: dict[str, Any] = {
            "protocol": wire.PROTOCOL_VERSION, "sdk_version": "1.0.0", "device_udid": udid,
            "bundle_id": self.bundle_id, "name": self.name, "pid": self.pid, "port": self.port,
            "secret": self.secret, "active": True, "started_at": "2026-09-17T08:00:00.000Z",
        }  # fmt: skip
        listing.update(more)
        return listing

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            line, *lines = head.decode("latin-1").split("\r\n")
            headers = {
                name.strip().lower(): value.strip()
                for name, _, value in (entry.partition(":") for entry in lines if entry)
            }
            self.requests.append(AppRequest(line, headers))
            writer.write(await self._answer(headers))
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    def _http(status: int, body: bytes, *headers: str) -> bytes:
        head = [f"HTTP/1.1 {status} Status", "Content-Type: application/json", *headers, "Connection: close"]
        return ("\r\n".join(head) + "\r\n\r\n").encode() + body

    def _error(self, status: int, code: str) -> bytes:
        body = json.dumps({"error": {"code": code, "message": code.replace("_", " ")}}).encode()
        return self._http(status, body, f"Content-Length: {len(body)}")

    async def _answer(self, headers: dict[str, str]) -> bytes:
        if headers.get("authorization") != f"Bearer {self.secret}":
            return self._error(401, "unauthorized")
        body = json.dumps(self.document).encode()
        sized = f"Content-Length: {len(body)}"
        match self.answer:
            case "inactive":
                return self._error(409, "inactive")
            case "refuse":
                return self._error(503, "busy")
            case "hang":
                await self._closing.wait()
                return b""
            case "hang_up":
                return b""
            case "not_json":
                return self._http(200, b"<html>", "Content-Length: 6")
            case "not_object":
                return self._http(200, b"[]", "Content-Length: 2")
            case "too_large":
                return self._http(200, b"", f"Content-Length: {wire.RESPONSE_MAX_BYTES + 1}")
            case "chunked":
                return self._http(200, b"0\r\n\r\n", "Transfer-Encoding: chunked")
            case "unsized":
                return self._http(200, body)
            case "unsized_too_large":
                return self._http(200, b" " * (wire.RESPONSE_MAX_BYTES + 1))
            case "long_headers":
                return self._http(200, body, "X-Padding: " + "x" * wire.HEADERS_MAX_BYTES, sized)
            case "not_http":
                return b"SSH-2.0-OpenSSH\r\n\r\n"
            case "bad_length":
                return self._http(200, body, "Content-Length: many")
        return self._http(200, body, sized)
