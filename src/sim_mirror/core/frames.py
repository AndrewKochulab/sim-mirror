# SPDX-License-Identifier: Apache-2.0
"""A device's screen, streamed to however many viewers watch it.

One `FrameHub` per running device. A viewer subscribes for JPEG or for H.264; the first subscriber of a kind starts
that kind's source, and the source stops `LINGER_S` after the last one leaves -- so a device nobody watches costs
nothing, and a window that reconnects within a moment does not restart anything.

* **JPEG** polls the connector's `screenshot` at the stream's frame rate, sized and encoded by the connector. A frame
  that hashes the same as the last is not sent, so a still screen is silent on the wire. A viewer holds only the
  newest frame: a slow one skips frames rather than falling behind, and one that joins is handed the frame already on
  screen.
* **H.264** relays the connector's Annex-B stream, which repeats its parameter sets and a key frame every
  `KEY_FRAME_S`. A viewer's first chunk is always one of those sync points, and a viewer that falls `QUEUE_MAX` chunks
  behind drops everything until the next one rather than decoding garbage.

A source that fails reports why through `on_trouble` and tries again after `RETRY_S`; the first frame after that
reports that the trouble is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Screen, ScreenSource

Kind = Literal["jpeg", "h264"]
KINDS: tuple[Kind, ...] = ("jpeg", "h264")
LINGER_S = 10.0
KEY_FRAME_S = 1.0
#: Bits per second an H.264 stream aims for, per megapixel streamed.
BITRATE_PER_MEGAPIXEL = 3_000_000
QUEUE_MAX = 120
#: How long a failing source waits before trying again: after the first failure, the second, and every one after.
RETRY_S = (1.0, 5.0, 30.0)

#: NAL unit type of a sequence parameter set, which every sync point in the stream starts with.
NAL_SPS = 7


@dataclass(frozen=True)
class StreamSettings:
    fps: int
    quality: int
    max_width: int

    @classmethod
    def from_config(cls, config: SimConfig, fps_limit: int | None = None) -> StreamSettings:
        """A config's stream settings, no faster than the connector can stream."""
        fps = config.stream_fps if fps_limit is None else min(config.stream_fps, fps_limit)
        return cls(fps=fps, quality=config.stream_quality, max_width=config.stream_max_width)


@dataclass(frozen=True)
class Frame:
    kind: Kind
    data: bytes
    width: int = 0
    height: int = 0


def nal_types(chunk: bytes) -> set[int]:
    """The NAL unit types that follow an Annex-B start code in this chunk."""
    found: set[int] = set()
    index = chunk.find(b"\x00\x00\x01")
    while 0 <= index < len(chunk) - 3:
        found.add(chunk[index + 3] & 0x1F)
        index = chunk.find(b"\x00\x00\x01", index + 3)
    return found


def is_sync_point(chunk: bytes) -> bool:
    """Whether a decoder can start at this chunk: it carries a sequence parameter set."""
    return NAL_SPS in nal_types(chunk)


class _Subscriber:
    def __init__(self, kind: Kind) -> None:
        self.kind: Kind = kind
        self._ready = asyncio.Event()
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._ready.set()


class LatestFrame(_Subscriber):
    """A JPEG viewer: only ever the newest frame."""

    def __init__(self) -> None:
        super().__init__("jpeg")
        self._frame: Frame | None = None

    def offer(self, frame: Frame) -> None:
        self._frame = frame
        self._ready.set()

    async def next(self) -> Frame | None:
        """The newest frame not yet taken, waiting for one; None once closed."""
        while self._frame is None and not self._closed:
            self._ready.clear()
            await self._ready.wait()
        frame, self._frame = self._frame, None
        return None if self._closed else frame


class KeyFrameQueue(_Subscriber):
    """An H.264 viewer: every chunk, in order, starting at a sync point."""

    def __init__(self, limit: int = QUEUE_MAX) -> None:
        super().__init__("h264")
        self._limit = limit
        self._chunks: deque[Frame] = deque()
        self._waiting = True

    def offer(self, frame: Frame) -> None:
        if len(self._chunks) >= self._limit:
            self._chunks.clear()
            self._waiting = True
        if self._waiting and not is_sync_point(frame.data):
            return
        self._waiting = False
        self._chunks.append(frame)
        self._ready.set()

    async def next(self) -> Frame | None:
        while not self._chunks and not self._closed:
            self._ready.clear()
            await self._ready.wait()
        return None if self._closed else self._chunks.popleft()


Subscriber = LatestFrame | KeyFrameQueue


class FrameHub:
    """One device's screen sources and the viewers they feed."""

    def __init__(
        self,
        source: ScreenSource,
        screen: Screen,
        settings: StreamSettings,
        *,
        on_trouble: Callable[[str | None], None] = lambda reason: None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source = source
        self._screen = screen
        self._settings = settings
        self._on_trouble = on_trouble
        self._clock = clock
        self._sleep = sleep
        self._subscribers: dict[Kind, set[Subscriber]] = {kind: set() for kind in KINDS}
        self._sources: dict[Kind, asyncio.Task[None]] = {}
        self._stops: dict[Kind, asyncio.Task[None]] = {}
        self._latest_jpeg: Frame | None = None
        self._troubled = False

    @property
    def settings(self) -> StreamSettings:
        return self._settings

    @property
    def viewers(self) -> int:
        return sum(len(subscribers) for subscribers in self._subscribers.values())

    def running(self, kind: Kind) -> bool:
        return kind in self._sources

    def subscribe(self, kind: str) -> Subscriber:
        if kind not in KINDS:
            raise ValueError(f"not a stream kind: {kind!r}")
        subscriber: Subscriber = LatestFrame() if kind == "jpeg" else KeyFrameQueue()
        self._subscribers[subscriber.kind].add(subscriber)
        stop = self._stops.pop(subscriber.kind, None)
        if stop is not None:
            stop.cancel()
        if subscriber.kind not in self._sources:
            self._start(subscriber.kind)
        elif isinstance(subscriber, LatestFrame) and self._latest_jpeg is not None:
            subscriber.offer(self._latest_jpeg)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        subscribers = self._subscribers[subscriber.kind]
        subscribers.discard(subscriber)
        subscriber.close()
        if not subscribers and subscriber.kind in self._sources and subscriber.kind not in self._stops:
            self._stops[subscriber.kind] = asyncio.get_running_loop().create_task(self._stop_later(subscriber.kind))

    def reconfigure(self, settings: StreamSettings) -> None:
        """Use new stream settings, restarting whatever is streaming so viewers see them at once."""
        if settings == self._settings:
            return
        self._settings = settings
        for kind in list(self._sources):
            self._halt(kind)
            self._start(kind)

    def rebind(self, source: ScreenSource, screen: Screen) -> None:
        """Take frames from a new source, restarting whatever is streaming; viewers stay subscribed."""
        self._source, self._screen = source, screen
        for kind in list(self._sources):
            self._halt(kind)
            self._start(kind)

    async def close(self) -> None:
        tasks = [*self._stops.values(), *self._sources.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._stops.clear()
        self._sources.clear()
        for subscribers in self._subscribers.values():
            for subscriber in subscribers:
                subscriber.close()
            subscribers.clear()

    # -- sources ------------------------------------------------------------------------------------------------

    def _start(self, kind: Kind) -> None:
        source = self._jpeg() if kind == "jpeg" else self._h264()
        self._sources[kind] = asyncio.get_running_loop().create_task(source)

    def _halt(self, kind: Kind) -> None:
        self._sources.pop(kind).cancel()
        if kind == "jpeg":
            self._latest_jpeg = None

    async def _stop_later(self, kind: Kind) -> None:
        # A viewer that comes back cancels this (`subscribe`), so reaching the end means nobody did.
        await self._sleep(LINGER_S)
        self._stops.pop(kind, None)
        self._halt(kind)

    def _publish(self, frame: Frame) -> None:
        if self._troubled:
            self._troubled = False
            self._on_trouble(None)
        for subscriber in list(self._subscribers[frame.kind]):
            subscriber.offer(frame)

    def _trouble(self, exc: ConnectorError, failures: int) -> float:
        self._troubled = True
        self._on_trouble(str(exc))
        return RETRY_S[min(failures, len(RETRY_S) - 1)]

    async def _jpeg(self) -> None:
        last: bytes | None = None
        failures = 0
        while True:
            started = self._clock()
            settings = self._settings
            try:
                shot = await self._source.screenshot(max_width=settings.max_width, quality=settings.quality)
            except ConnectorError as exc:
                wait = self._trouble(exc, failures)
                failures += 1
                last = None
            else:
                failures = 0
                digest = hashlib.blake2b(shot.jpeg, digest_size=8).digest()
                if digest != last:
                    last = digest
                    self._latest_jpeg = Frame("jpeg", shot.jpeg, shot.width, shot.height)
                    self._publish(self._latest_jpeg)
                wait = 1.0 / settings.fps - (self._clock() - started)
            if wait > 0:
                await self._sleep(wait)

    def h264_scale(self) -> float:
        """The fraction of the screen's pixels an H.264 stream carries, so it is no wider than `max_width`."""
        return min(1.0, self._settings.max_width / self._screen.width_px) if self._screen.width_px else 1.0

    async def _h264(self) -> None:
        failures = 0
        while True:
            settings = self._settings
            scale = self.h264_scale()
            megapixels = self._screen.width_px * self._screen.height_px * scale * scale / 1_000_000
            bitrate = max(250_000, int(megapixels * BITRATE_PER_MEGAPIXEL))
            try:
                async for chunk in self._source.h264(fps=settings.fps, scale=scale, key_frame_s=KEY_FRAME_S,
                                                     bitrate=bitrate):  # fmt: skip
                    failures = 0
                    self._publish(Frame("h264", chunk))
                raise ConnectorError("the video stream ended")
            except ConnectorError as exc:
                wait = self._trouble(exc, failures)
                failures += 1
            await self._sleep(wait)
