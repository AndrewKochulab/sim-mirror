# SPDX-License-Identifier: Apache-2.0
"""A real device's screen by screenshot, through devicectl: what the iphone connector shows with no cable and no
WebDriverAgent.

devicectl writes a whole screenshot as a PNG, about 0.75 s at a time over Wi-Fi, so the frame hub asks for at most
`FPS_LIMIT` a second; each is made a JPEG -- no wider than asked -- with macOS's ``sips``. devicectl says the screen's
size and scale exactly, so its points are not estimated.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Protocol

from sim_mirror.connectors.base import ConnectorError, Crop, RefusedStream, Screen, Shot
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display
from sim_mirror.platform.images import jpeg_size, png_to_jpeg

#: How many screenshots a second devicectl can take and send.
FPS_LIMIT = 1


class Convert(Protocol):
    def __call__(self, data: bytes, *, max_width: int | None, quality: int) -> Awaitable[bytes | None]: ...


def screen_of(display: Display) -> Screen:
    """A device's screen as SimMirror measures it: pixels, and points at the device's own scale."""
    return Screen(
        display.width_px,
        display.height_px,
        round(display.width_px / display.scale),
        round(display.height_px / display.scale),
        display.scale,
    )


class DevicectlScreen:
    """A real device's screen, one devicectl screenshot at a time."""

    def __init__(self, devicectl: Devicectl, udid: str, display: Display, *, convert: Convert = png_to_jpeg) -> None:
        self._devicectl = devicectl
        self._udid = udid
        self._screen = screen_of(display)
        self._convert = convert

    async def describe(self) -> Screen:
        return self._screen

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        if crop is not None:
            raise ConnectorError("a screenshot of a region needs the device's cable or WebDriverAgent")
        with tempfile.TemporaryDirectory(prefix="sim-mirror-shot-") as folder:
            path = Path(folder) / "screen.png"
            try:
                await self._devicectl.screenshot(self._udid, path)
            except DevicectlError as exc:
                raise ConnectorError(f"taking a screenshot failed: {exc}") from exc
            png = path.read_bytes() if path.is_file() else b""
        if not png:
            raise ConnectorError("taking a screenshot failed: devicectl wrote no picture")
        narrower = max_width if self._screen.width_px > max_width else None
        data = await self._convert(png, max_width=narrower, quality=quality)
        size = jpeg_size(data) if data is not None else None
        if data is None or size is None:
            raise ConnectorError("taking a screenshot failed: its picture could not be made a JPEG")
        return Shot(data, size[0], size[1])

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        return RefusedStream("a live H.264 picture of a real device needs its cable")
