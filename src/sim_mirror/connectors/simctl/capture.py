# SPDX-License-Identifier: Apache-2.0
"""A device's screen through ``simctl io screenshot``: what the simctl connector can show.

simctl writes a whole screenshot and nothing else -- no scaling, no region, no stream -- so this is a `ScreenSource`
that polls it (the frame hub asks at most `connector.FPS_LIMIT` times a second), reads the image's size from its
bytes, and shrinks it with macOS's ``sips`` when a narrower one is asked for. simctl says nothing of a screen's
points, so they are estimated from its shape: a tall modern iPhone is drawn at 3x, anything squarer at 2x.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from typing import Protocol

from sim_mirror.connectors.base import ConnectorError, Crop, Screen, Shot
from sim_mirror.platform.images import jpeg_size, resize_jpeg
from sim_mirror.platform.simctl import Simctl, SimctlError

#: A screen at least this much taller than it is wide is a modern iPhone's, drawn at 3x.
TALL = 1.9


class Resize(Protocol):
    def __call__(self, data: bytes, *, max_width: int, quality: int) -> Awaitable[bytes | None]: ...


def estimated_scale(width_px: int, height_px: int) -> float:
    long, short = max(width_px, height_px), min(width_px, height_px)
    return 3.0 if short and long / short >= TALL else 2.0


class _Refused:
    """An async iterator whose first step refuses: a stream this connector cannot give."""

    def __init__(self, message: str) -> None:
        self._message = message

    def __aiter__(self) -> _Refused:
        return self

    async def __anext__(self) -> bytes:
        raise ConnectorError(self._message)


class SimctlScreen:
    def __init__(self, simctl: Simctl, udid: str, *, resize: Resize = resize_jpeg) -> None:
        self._simctl = simctl
        self._udid = udid
        self._resize = resize

    async def _capture(self) -> tuple[bytes, int, int]:
        try:
            data = await self._simctl.screenshot(self._udid)
        except SimctlError as exc:
            raise ConnectorError(f"taking a screenshot failed: {exc}") from exc
        size = jpeg_size(data)
        if size is None:
            raise ConnectorError("taking a screenshot failed: simctl wrote something that is not a JPEG")
        return data, size[0], size[1]

    async def describe(self) -> Screen:
        _data, width, height = await self._capture()
        scale = estimated_scale(width, height)
        return Screen(width, height, round(width / scale), round(height / scale), scale)

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        if crop is not None:
            raise ConnectorError("a screenshot of a region needs the idb connector")
        data, width, height = await self._capture()
        if width <= max_width:
            return Shot(data, width, height)
        smaller = await self._resize(data, max_width=max_width, quality=quality)
        size = jpeg_size(smaller) if smaller is not None else None
        if smaller is None or size is None:
            return Shot(data, width, height)
        return Shot(smaller, size[0], size[1])

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        return _Refused("an H.264 stream needs the idb connector")
