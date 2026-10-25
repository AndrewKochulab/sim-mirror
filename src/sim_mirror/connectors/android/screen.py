# SPDX-License-Identifier: Apache-2.0
"""An Android device's screen through adb: its size, and screenshots.

A screenshot is ``screencap -j``, a JPEG the device encodes itself -- measured on an API 36 emulator, 181 ms -- or, on
an Android whose ``screencap`` has no JPEG, ``screencap -p``'s PNG (825 ms). Either is the turned display's picture,
turned back to the portrait screen here (`geometry`). Its H.264 stream is scrcpy's (`scrcpy_roles.ScrcpyScreen`); this
screen has none, and says what would give one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sim_mirror.connectors.android.geometry import Rotation, portrait_jpeg
from sim_mirror.connectors.base import ConnectorError, Crop, RefusedStream, Screen, Shot
from sim_mirror.platform.adb import Adb, AdbError

JPEG_MAGIC = b"\xff\xd8"
#: How long a screenshot may take.
SHOT_TIMEOUT_S = 15.0
NO_STREAM = (
    "Without scrcpy, the screen is shown a screenshot at a time: set scrcpy up (Set up touch, or sim-mirror android "
    "setup) for a live H.264 stream"
)


class AdbScreen:
    """An Android device's screen, a screenshot at a time."""

    def __init__(self, adb: Adb, serial: str, screen: Screen, rotation: Rotation) -> None:
        self._adb = adb
        self._serial = serial
        self._screen = screen
        self._rotation = rotation
        #: Whether ``screencap -j`` gives a JPEG here; None until it is first asked.
        self._jpeg: bool | None = None

    async def describe(self) -> Screen:
        return self._screen

    async def _picture(self) -> bytes:
        if self._jpeg is not False:
            picture = await self._adb.exec_out(self._serial, ["screencap", "-j"], timeout=SHOT_TIMEOUT_S)
            self._jpeg = picture.startswith(JPEG_MAGIC)
            if self._jpeg:
                return picture
        return await self._adb.exec_out(self._serial, ["screencap", "-p"], timeout=SHOT_TIMEOUT_S)

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        try:
            picture = await self._picture()
        except AdbError as exc:
            raise ConnectorError(f"the device's screen could not be read: {exc}") from exc
        scale = self._screen.scale
        box = None
        if crop is not None:
            box = (
                round(crop.x * scale),
                round(crop.y * scale),
                round((crop.x + crop.width) * scale),
                round((crop.y + crop.height) * scale),
            )
        jpeg, width, height = portrait_jpeg(
            picture, await self._rotation.quarters(), max_width=max_width, quality=quality, crop=box
        )
        return Shot(jpeg, width, height)

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        return RefusedStream(NO_STREAM)
