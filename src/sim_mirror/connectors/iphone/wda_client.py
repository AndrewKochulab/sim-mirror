# SPDX-License-Identifier: Apache-2.0
"""Talking to WebDriverAgent on a device: HTTP/1.1 with JSON, over the device's cable.

SimMirror runs WebDriverAgent bound to the device's own loopback (``USE_IP=127.0.0.1``), so nothing on the network
can reach it: the Mac reaches it through usbmuxd, which opens a device port as a plain socket
(`platform.usbmux`). Each request opens its own connection and closes it -- usbmuxd connects in about a
millisecond -- so a request that dies with its cable leaves nothing half-read behind.

A WebDriver session is made the first time one is needed, and made again when WebDriverAgent says the old one is gone,
as it does after it restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.platform.usbmux import Usbmux, UsbmuxError

#: WebDriverAgent's HTTP port on the device (`build.wda`).
HTTP_PORT = 8100
#: How long a request may take: reading a busy screen's element tree takes seconds.
REQUEST_TIMEOUT_S = 30.0
#: The largest answer read: an element tree of a long list runs to a few megabytes.
ANSWER_MAX = 32 << 20

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
#: Opens a connection to a port on the device.
Opener = Callable[[int], Awaitable[Streams]]


class WdaError(ConnectorError):
    """A request WebDriverAgent refused, or one that did not reach it."""

    def __init__(self, message: str, *, error: str = "", status: int = 0) -> None:
        super().__init__(message)
        #: WebDriver's name for what went wrong -- ``invalid session id``, say -- or "" when it did not get that far.
        self.error = error
        self.status = status


def usbmux_opener(udid: str, usbmux: Usbmux | None = None) -> Opener:
    """Connections to the device's ports through its cable."""
    mux = usbmux or Usbmux()

    def connect(port: int) -> Any:
        device = mux.find(udid)
        if device is None:
            raise UsbmuxError(f"{udid} is not plugged in by cable")
        return mux.connect(device.device_id, port)

    async def open_port(port: int) -> Streams:
        try:
            sock = await asyncio.to_thread(connect, port)
        except UsbmuxError as exc:
            raise WdaError(f"WebDriverAgent cannot be reached: {exc}") from exc
        sock.setblocking(False)
        return await asyncio.open_connection(sock=sock, limit=ANSWER_MAX)

    return open_port


async def exchange(opener: Opener, port: int, method: str, path: str, body: Any = None) -> tuple[int, bytes]:
    """One HTTP/1.1 request on its own connection: the status and body of its answer."""
    try:
        reader, writer = await opener(port)
    except OSError as exc:
        raise WdaError(f"WebDriverAgent does not answer on port {port}: {exc}") from exc
    try:
        payload = b"" if body is None else json.dumps(body).encode()
        head = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
        )
        writer.write(head.encode() + payload)
        await writer.drain()
        status_line = await reader.readline()
        parts = status_line.decode("latin-1").split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
            raise WdaError(f"WebDriverAgent answered what is not HTTP: {status_line[:80]!r}")
        headers: dict[str, str] = {}
        while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
            name, _, value = line.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        length = headers.get("content-length")
        if length is not None and length.isdigit():
            if int(length) > ANSWER_MAX:
                raise WdaError(f"WebDriverAgent's answer is larger than {ANSWER_MAX >> 20} MB")
            data = await reader.readexactly(int(length))
        else:
            data = await reader.read(ANSWER_MAX)
        return int(parts[1]), data
    except (OSError, asyncio.IncompleteReadError) as exc:
        raise WdaError(f"WebDriverAgent stopped answering: {exc}") from exc
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def answer_of(status: int, data: bytes) -> dict[str, Any]:
    """A WebDriver answer as JSON, or the error it says. Raises `WdaError`."""
    try:
        document = json.loads(data) if data else {}
    except ValueError as exc:
        raise WdaError(f"WebDriverAgent answered what is not JSON (HTTP {status})", status=status) from exc
    if not isinstance(document, dict):
        raise WdaError(f"WebDriverAgent answered what is not a WebDriver answer (HTTP {status})", status=status)
    value = document.get("value")
    if isinstance(value, dict) and "error" in value:
        message = str(value.get("message") or value["error"]).splitlines()[0]
        raise WdaError(f"WebDriverAgent refused it: {message}", error=str(value["error"]), status=status)
    if status >= 400:
        raise WdaError(f"WebDriverAgent refused it (HTTP {status})", status=status)
    return document


class WdaClient:
    """WebDriverAgent on one device, and the WebDriver session SimMirror keeps with it."""

    def __init__(self, opener: Opener, *, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._opener = opener
        self._timeout_s = timeout_s
        self._session: str | None = None
        self._lock = asyncio.Lock()

    async def call(self, method: str, path: str, body: Any = None, *, timeout_s: float | None = None) -> Any:
        """A request to a path of WebDriverAgent's own, answering its ``value``."""
        try:
            status, data = await asyncio.wait_for(
                exchange(self._opener, HTTP_PORT, method, path, body), timeout=timeout_s or self._timeout_s
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise WdaError(f"WebDriverAgent did not answer {method} {path} in time") from exc
        return answer_of(status, data).get("value")

    async def status(self) -> dict[str, Any] | None:
        """What WebDriverAgent says of itself, or None when it does not answer."""
        try:
            value = await self.call("GET", "/status", timeout_s=5.0)
        except WdaError:
            return None
        return value if isinstance(value, dict) else {}

    async def session(self) -> str:
        """The WebDriver session, made the first time it is needed."""
        async with self._lock:
            if self._session is None:
                status, data = await exchange(
                    self._opener, HTTP_PORT, "POST", "/session", {"capabilities": {"alwaysMatch": {}}}
                )
                document = answer_of(status, data)
                value = document.get("value")
                made = document.get("sessionId") or (value.get("sessionId") if isinstance(value, dict) else None)
                if not isinstance(made, str) or not made:
                    raise WdaError("WebDriverAgent made no session")
                self._session = made
            return self._session

    async def in_session(self, method: str, path: str, body: Any = None, *, timeout_s: float | None = None) -> Any:
        """A request under the session -- `path` goes after ``/session/<id>`` -- made again in a new session when
        WebDriverAgent has forgotten the old one."""
        for attempt in (1, 2):
            session = await self.session()
            try:
                return await self.call(method, f"/session/{session}{path}", body, timeout_s=timeout_s)
            except WdaError as exc:
                if exc.error != "invalid session id" or attempt == 2:
                    raise
                async with self._lock:
                    # Another request may have made a new one already.
                    self._session = None if self._session == session else self._session
        raise AssertionError("unreachable")  # pragma: no cover
