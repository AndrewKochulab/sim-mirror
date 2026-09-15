# SPDX-License-Identifier: Apache-2.0
"""A WebSocket client that drives an ASGI app in the test's own event loop.

Starlette's test client runs the app on a thread with a loop of its own, where a `DeviceManager` built in the test's
loop cannot be used. HTTP needs nothing more than ``httpx.ASGITransport``; this is the same for a socket, for
SimMirror's tests and a host's.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from types import TracebackType
from typing import Any

from starlette.types import ASGIApp, Message

HOST = "127.0.0.1:7466"


class AsgiSocket:
    """One socket to `path` on `app`: connect, then read what the app sends and send it messages."""

    def __init__(
        self, app: ASGIApp, path: str, *, query: str = "", headers: Sequence[tuple[str, str]] = (("host", HOST),)
    ) -> None:
        self._app = app
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
            "server": ("127.0.0.1", 7466),
            "client": ("127.0.0.1", 50000),
            "subprotocols": [],
        }
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        #: The code and reason the app closed with, once it has.
        self.closed: tuple[int, str] | None = None

    async def _serve(self) -> None:
        await self._app(self._scope, self._to_app.get, self._send)

    async def __aenter__(self) -> AsgiSocket:
        self._task = asyncio.get_running_loop().create_task(self._serve())
        await self._to_app.put({"type": "websocket.connect"})
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        await self.leave()

    async def _send(self, message: Message) -> None:
        if message["type"] == "websocket.close":
            self.closed = (int(message.get("code", 1000)), str(message.get("reason") or ""))
        await self._from_app.put(message)

    async def next(self, timeout: float = 2.0) -> Message:
        """The next message the app sent."""
        return await asyncio.wait_for(self._from_app.get(), timeout)

    async def accepted(self) -> bool:
        """Whether the app accepted the socket rather than closing it first."""
        return bool((await self.next())["type"] == "websocket.accept")

    async def text(self) -> Any:
        """The next text message, as JSON; None when the app closed instead."""
        message = await self.next()
        return json.loads(message["text"]) if message["type"] == "websocket.send" and "text" in message else None

    async def say(self, value: object) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(value)})

    async def leave(self) -> None:
        """Disconnect, and wait for the app to finish with the socket."""
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)

    async def ended(self) -> None:
        """Wait for the app to finish with the socket on its own."""
        if self._task is not None:
            await asyncio.wait_for(self._task, 5)
            self._task = None
