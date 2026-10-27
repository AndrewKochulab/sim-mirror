# SPDX-License-Identifier: Apache-2.0
"""Talking to WebDriverAgent on a device: HTTP/1.1 with JSON, over the device's cable.

SimMirror runs WebDriverAgent bound to the device's own loopback (``USE_IP=127.0.0.1``), so nothing on the network
can reach it: the Mac reaches it through usbmuxd, which opens a device port as a plain socket
(`platform.usbmux`). Each request opens its own connection and closes it -- usbmuxd connects in about a
millisecond -- so a request that dies with its cable leaves nothing half-read behind.

A WebDriver session is made the first time one is needed, and made again when WebDriverAgent says the old one is gone,
as it does after it restarts.

**WebDriverAgent serves one request at a time.** Every route runs on its main queue (``FBWebServer`` makes it the
route queue), and a request SimMirror stopped waiting for is still served to its end: an element tree given up on
after five seconds goes on being built, and every request after it -- the next tree, a tap, ``/status``, a screenshot
-- waits behind it. So a request not answered in time leaves the client *busy* (`WdaClient.idle`) until WebDriverAgent
answers ``/status`` again, which it does only once what is ahead of it is done: a reader can go elsewhere meanwhile,
rather than add to the queue, and a touch waits a moment for it (`WdaClient.free_within`), rather than land late.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.platform.usbmux import Usbmux, UsbmuxError

logger = logging.getLogger(__name__)

#: WebDriverAgent's HTTP port on the device (`build.wda`).
HTTP_PORT = 8100
#: What SimMirror's session asks of WebDriverAgent: to act at once. By default it waits before every action for the
#: app to go idle -- up to 10 s -- and for animations to settle, 2 s more. Measured on an iPhone 14 Pro on iOS 26.3: a
#: tap took 1.85 to 2.5 s with those waits, and 0.67 s without; what is left is XCTest performing the touch.
QUICK_SETTINGS: dict[str, Any] = {"waitForIdleTimeout": 0, "animationCoolOffTimeout": 0}
QUICK_CAPABILITIES: dict[str, Any] = {"shouldWaitForQuiescence": False, **QUICK_SETTINGS}
#: WebDriverAgent's names, in ``/appium/settings``, for how deep it reads an element tree and how many children of
#: each element it reads.
MAX_DEPTH_SETTING = "snapshotMaxDepth"
MAX_CHILDREN_SETTING = "snapshotMaxChildren"
#: How deep a tree is read: WebDriverAgent's own default, which a new session resets it to, given again so a session
#: another tool changed reads as deep as SimMirror's own. WebDriverAgent calls 15 to 100 reasonable.
SOURCE_MAX_DEPTH = 50
#: How many children of each element a tree is read with, unless `real_devices.wda.source_max_children` says
#: otherwise. WebDriverAgent's own default has no bound, and a table lists every row -- those scrolled off screen too,
#: each read whole -- so a long one takes it longer than a snapshot waits. 200 rows of 44 points are ten screens of an
#: iPhone, and a list that reuses its cells never holds that many.
SOURCE_MAX_CHILDREN = 200
#: What each session is given: to act at once, and to read a tree within bounds.
SESSION_SETTINGS: dict[str, Any] = {
    **QUICK_SETTINGS,
    MAX_DEPTH_SETTING: SOURCE_MAX_DEPTH,
    MAX_CHILDREN_SETTING: SOURCE_MAX_CHILDREN,
}
#: How long a request may take: reading a busy screen's element tree takes seconds.
REQUEST_TIMEOUT_S = 30.0
#: How long a busy WebDriverAgent has to answer ``/status`` to be taken as free again, and how long after it did not it
#: is still taken as busy without being asked.
PROBE_TIMEOUT_S = 1.0
#: The largest answer read: an element tree of a long list runs to a few megabytes.
ANSWER_MAX = 32 << 20
#: An answer longer than this is parsed on a thread, off the event loop every device and agent shares. Measured with
#: Python 3.13 on an Apple Silicon Mac: a 6 MB tree of 15,000 elements parsed in 26 ms.
PARSE_ON_THREAD = 256 << 10
#: Said when WebDriverAgent closed a connection before it answered a word.
CLOSED = "WebDriverAgent closed the connection without answering; it may have restarted"

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


class WdaTimeout(WdaError):
    """A request WebDriverAgent did not answer in time -- and goes on serving."""


class WdaClosed(WdaError):
    """A connection WebDriverAgent closed before it answered."""


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
        if not status_line:
            raise WdaClosed(CLOSED)
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


def _value(status: int, data: bytes, shape: Callable[[Any], Any] | None) -> Any:
    """The ``value`` of a WebDriver answer, made into what `shape` makes of it."""
    value = answer_of(status, data).get("value")
    return value if shape is None else shape(value)


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
    """WebDriverAgent on one device, and the WebDriver session SimMirror keeps with it, given `settings` when it is
    made. The keyword arguments after `settings` are test seams."""

    def __init__(
        self,
        opener: Opener,
        *,
        timeout_s: float = REQUEST_TIMEOUT_S,
        settings: Mapping[str, Any] = SESSION_SETTINGS,
        probe_timeout_s: float = PROBE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._opener = opener
        self._timeout_s = timeout_s
        self._settings = dict(settings)
        self._probe_timeout_s = probe_timeout_s
        self._clock = clock
        self._sleep = sleep
        self._session: str | None = None
        self._lock = asyncio.Lock()
        #: The request WebDriverAgent may still be serving after it was given up on; None when it is free.
        self._busy: str | None = None
        #: When WebDriverAgent last did not answer ``/status`` while it was busy.
        self._probed: float | None = None
        self._probing = asyncio.Lock()

    @property
    def busy(self) -> str | None:
        """The request WebDriverAgent may still be serving after SimMirror stopped waiting for it, or None."""
        return self._busy

    async def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout_s: float | None = None,
        shape: Callable[[Any], Any] | None = None,
    ) -> Any:
        """A request to a path of WebDriverAgent's own, answering its ``value`` -- or what `shape` makes of it, made on
        a thread with the parsing when the answer is long. One not answered in time leaves the client busy (`idle`)."""
        try:
            status, data = await asyncio.wait_for(
                self._exchange(method, path, body), timeout=timeout_s or self._timeout_s
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            if self._busy is None:
                logger.info("WebDriverAgent did not answer %s %s in time, and goes on serving it", method, path)
                self._busy = f"{method} {path}"
            raise WdaTimeout(f"WebDriverAgent did not answer {method} {path} in time") from exc
        if len(data) > PARSE_ON_THREAD:
            return await asyncio.to_thread(_value, status, data, shape)
        return _value(status, data, shape)

    async def _exchange(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
        """One exchange -- made once more when WebDriverAgent closed the connection on a request that only reads, which
        changes nothing if it was served after all."""
        try:
            return await exchange(self._opener, HTTP_PORT, method, path, body)
        except WdaClosed:
            if method != "GET":
                raise
            logger.info("WebDriverAgent closed the connection on GET %s; asking once more", path)
            return await exchange(self._opener, HTTP_PORT, method, path, body)

    async def status(self) -> dict[str, Any] | None:
        """What WebDriverAgent says of itself, or None when it does not answer."""
        try:
            value = await self.call("GET", "/status", timeout_s=5.0)
        except WdaError:
            return None
        return value if isinstance(value, dict) else {}

    async def idle(self) -> bool:
        """Whether WebDriverAgent is free to serve a request now, not still serving one SimMirror gave up on.

        Free unless a request went unanswered; after that, once it answers ``/status`` within `PROBE_TIMEOUT_S` --
        which, serving one request at a time, it does only once what was ahead of it is done. Asked at most once in
        that time: a caller in between is answered as the last one was, so the queue is not added to.
        """
        if self._busy is None:
            return True
        async with self._probing:
            return await self._probe()

    async def free_within(self, wait_s: float) -> bool:
        """Whether WebDriverAgent is free to serve a request now, or is within `wait_s`: asked as `idle` asks, at most
        once every `PROBE_TIMEOUT_S`, until it answers or the time is up -- a request sent sooner would only wait behind
        what it is still serving."""
        deadline = self._clock() + wait_s
        while not await self.idle():
            left = deadline - self._clock()
            if left <= 0:
                return False
            await self._sleep(min(self._probe_timeout_s, left))
        return True

    async def _probe(self) -> bool:
        busy = self._busy
        if busy is None:
            # Another caller's probe found it free meanwhile.
            return True
        if self._probed is not None and self._clock() - self._probed < self._probe_timeout_s:
            return False
        try:
            await self.call("GET", "/status", timeout_s=self._probe_timeout_s)
        except WdaError:
            self._probed = self._clock()
            return False
        logger.info("WebDriverAgent answers again: it finished %s", busy)
        self._busy = self._probed = None
        return True

    async def configure(self, settings: Mapping[str, Any]) -> None:
        """Give every session these settings from now on, the one there is now too."""
        changed = {key: value for key, value in settings.items() if self._settings.get(key) != value}
        self._settings.update(changed)
        if changed and self._session is not None:
            await self._give(self._session, changed)

    async def _give(self, session: str, settings: Mapping[str, Any]) -> None:
        try:
            await self.call("POST", f"/session/{session}/appium/settings", {"settings": dict(settings)})
        except WdaError as exc:
            # Slower, then, not broken: every action waits for the app to go idle first, and a tree is read whole.
            logger.warning("WebDriverAgent did not take SimMirror's settings: %s", exc)

    async def session(self) -> str:
        """The WebDriver session, made the first time it is needed."""
        async with self._lock:
            if self._session is None:
                status, data = await exchange(
                    self._opener, HTTP_PORT, "POST", "/session", {"capabilities": {"alwaysMatch": QUICK_CAPABILITIES}}
                )
                document = answer_of(status, data)
                value = document.get("value")
                made = document.get("sessionId") or (value.get("sessionId") if isinstance(value, dict) else None)
                if not isinstance(made, str) or not made:
                    raise WdaError("WebDriverAgent made no session")
                await self._give(made, self._settings)
                self._session = made
            return self._session

    async def in_session(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout_s: float | None = None,
        shape: Callable[[Any], Any] | None = None,
    ) -> Any:
        """A request under the session -- `path` goes after ``/session/<id>`` -- made again in a new session when
        WebDriverAgent has forgotten the old one."""
        for attempt in (1, 2):
            session = await self.session()
            try:
                return await self.call(method, f"/session/{session}{path}", body, timeout_s=timeout_s, shape=shape)
            except WdaError as exc:
                if exc.error != "invalid session id" or attempt == 2:
                    raise
                async with self._lock:
                    # Another request may have made a new one already.
                    self._session = None if self._session == session else self._session
        raise AssertionError("unreachable")  # pragma: no cover
