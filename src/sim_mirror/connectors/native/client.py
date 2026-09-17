# SPDX-License-Identifier: Apache-2.0
"""A booted device through the native helper's socket: its screen, its input and what is on it.

One `HelperClient` per helper, which is one per booted device. It keeps separate connections so that one kind of
traffic never waits behind another:

* **control** -- the hello, ``describe``, screenshots and the accessibility document, answered by request id, so a
  snapshot and a screenshot asked for at once are answered at once;
* **input** -- touches, buttons and keys, one event at a time and in order, so a person's drag is never held up by a
  video frame being written;
* **a connection per stream** -- H.264 access units until the stream is let go of, which closes its connection.

A call that fails -- the helper gone, the device shut down under it, a request it refuses -- raises `ConnectorError`
with what the helper said, or that it did not answer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.connectors.native import wire

HELLO_TIMEOUT_S = 15.0
DESCRIBE_TIMEOUT_S = 5.0
SCREENSHOT_TIMEOUT_S = 5.0
ACCESSIBILITY_TIMEOUT_S = 10.0
INPUT_TIMEOUT_S = 5.0

Open = Callable[[str], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def open_unix(path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_unix_connection(path, limit=wire.MAX_FRAME)


@dataclass(frozen=True)
class Hello:
    """Who a helper is and what it reached."""

    wire: int
    version: str
    core_simulator: str | None
    #: The transport input goes through, or None when input cannot reach the device.
    hid: str | None
    reasons: tuple[str, ...]
    screen: Screen

    @classmethod
    def read(cls, document: dict[str, Any]) -> Hello:
        try:
            screen = document["screen"]
            return cls(
                wire=int(document["wire"]),
                version=str(document["version"]),
                core_simulator=document.get("core_simulator"),
                hid=document.get("hid"),
                reasons=tuple(str(reason) for reason in document.get("reasons") or ()),
                screen=_screen(screen),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorError(f"the native helper's hello cannot be read: {exc}") from exc


def _screen(document: dict[str, Any]) -> Screen:
    return Screen(
        width_px=int(document["width_px"]),
        height_px=int(document["height_px"]),
        width_pt=int(document["width_pt"]),
        height_pt=int(document["height_pt"]),
        scale=float(document["scale"]),
    )


@contextlib.contextmanager
def _failures(what: str) -> Iterator[None]:
    try:
        yield
    except ConnectorError:
        raise
    except (OSError, EOFError, wire.WireError, asyncio.TimeoutError) as exc:
        detail = str(exc) or type(exc).__name__
        raise ConnectorError(f"{what} failed: the native helper did not answer ({detail})") from exc


class _Connection:
    """One connection to the helper: requests out, and each answer handed to the request that asked for it."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._pending: dict[int, asyncio.Future[wire.Frame]] = {}
        self._next_id = 0
        self._reading = asyncio.ensure_future(self._read())

    @property
    def closed(self) -> bool:
        return self._reading.done()

    async def ask(self, op: str, timeout: float, **fields: Any) -> wire.Frame:
        self._next_id += 1
        request_id = self._next_id
        answer: asyncio.Future[wire.Frame] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = answer
        try:
            self._writer.write(wire.request(request_id, op, **fields))
            await self._writer.drain()
            frame = await asyncio.wait_for(answer, timeout)
        finally:
            self._pending.pop(request_id, None)
        if frame.kind == wire.FAILURE:
            raise ConnectorError(str(frame.document().get("message") or f"the native helper refused {op}"))
        return frame

    async def _read(self) -> None:
        failure: BaseException = EOFError("the native helper closed the connection")
        try:
            while (frame := await wire.read_frame(self._reader)) is not None:
                waiting = self._pending.get(frame.id)
                if waiting is not None and not waiting.done():
                    waiting.set_result(frame)
        except (OSError, wire.WireError) as exc:
            failure = exc
        finally:
            for waiting in self._pending.values():
                if not waiting.done():
                    waiting.set_exception(failure)

    async def close(self) -> None:
        self._reading.cancel()
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reading


class HelperClient:
    """A booted device, through its native helper."""

    def __init__(self, socket_path: str, *, open_connection: Open = open_unix) -> None:
        self._path = socket_path
        self._open = open_connection
        self._control: _Connection | None = None
        self._input: _Connection | None = None
        self._control_lock = asyncio.Lock()
        self._input_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    def at(cls, socket_path: str) -> HelperClient:
        """The client for the helper serving on this unix socket."""
        return cls(socket_path)

    async def _connection(self, current: _Connection | None) -> _Connection:
        if self._closed:
            raise ConnectorError("the native helper has been let go of")
        if current is not None:
            if not current.closed:
                return current
            # The helper closed it, or it broke: its socket goes before another is opened.
            await current.close()
        reader, writer = await self._open(self._path)
        return _Connection(reader, writer)

    async def _ask_control(self, what: str, op: str, timeout: float, **fields: Any) -> wire.Frame:
        with _failures(what):
            async with self._control_lock:
                self._control = connection = await self._connection(self._control)
            return await connection.ask(op, timeout, **fields)

    async def hello(self, timeout: float = HELLO_TIMEOUT_S) -> Hello:
        """Who the helper is. It answers once it has opened what the device's first picture needs."""
        frame = await self._ask_control("greeting the native helper", "hello", timeout)
        return Hello.read(frame.document())

    async def describe(self) -> Screen:
        frame = await self._ask_control("describing the device", "describe", DESCRIBE_TIMEOUT_S)
        try:
            return _screen(frame.document())
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorError(f"describing the device failed: the answer cannot be read ({exc})") from exc

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        fields: dict[str, Any] = {"max_width": max_width, "quality": quality}
        if crop is not None:
            fields["crop"] = {"x": crop.x, "y": crop.y, "width": crop.width, "height": crop.height}
        frame = await self._ask_control("taking a screenshot", "screenshot", SCREENSHOT_TIMEOUT_S, **fields)
        size = frame.document()
        return Shot(jpeg=frame.blob, width=int(size.get("width") or 0), height=int(size.get("height") or 0))

    async def accessibility(self) -> dict[str, Any]:
        frame = await self._ask_control("reading the screen", "accessibility", ACCESSIBILITY_TIMEOUT_S)
        return frame.document()

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        with _failures("sending input"):
            async for event in events:
                async with self._input_lock:
                    self._input = connection = await self._connection(self._input)
                    await connection.ask("hid", INPUT_TIMEOUT_S, events=[_event(event)])

    async def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        with _failures("the video stream"):
            if self._closed:
                raise ConnectorError("the native helper has been let go of")
            reader, writer = await self._open(self._path)
            try:
                writer.write(wire.request(1, "stream", fps=fps, scale=scale, key_frame_s=key_frame_s, bitrate=bitrate))
                await writer.drain()
                while (frame := await wire.read_frame(reader)) is not None:
                    if frame.kind == wire.CHUNK:
                        yield frame.blob
                    elif frame.kind == wire.FAILURE:
                        raise ConnectorError(f"the video stream failed: {frame.document().get('message')}")
                    else:
                        break
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def close(self) -> None:
        self._closed = True
        for connection in (self._control, self._input):
            if connection is not None:
                await connection.close()
        self._control = self._input = None


def _event(event: HidEvent) -> dict[str, Any]:
    if event.kind == "touch":
        return {"kind": "touch", "phase": event.phase, "x": event.x, "y": event.y}
    if event.kind == "button":
        return {"kind": "button", "phase": event.phase, "button": event.button}
    return {"kind": "key", "phase": event.phase, "code": event.code}
