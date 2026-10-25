# SPDX-License-Identifier: Apache-2.0
"""What scrcpy's server does for an Android session: the live H.264 screen, touches, keys and text.

**The screen** is a video server per stream, started for the stream's frame rate, size and bitrate and ended with it.
The device's capture is kept upright (``capture_orientation=@0``), so the stream is the portrait screen whichever way
the display is turned. Android's encoder sends frames only as the screen changes, and a key frame seldom when they
trickle -- a blinking cursor's three a second came with none in forty seconds (measured on API 36). A viewer that
joins is handed the pictures since the last key frame (`core.frames`), so none is needed until there are nearly as
many as that keeps: after `KEYLESS_MAX` frames without one, the server is told to capture again, which starts with
one -- no more than once each `RESET_GAP_S`. Asking that each second, as this once did, made scrcpy rebuild its
virtual display each time, and an emulator's adb connection dropped within a minute (measured on emulator 36.4).
Screenshots stay adb's (`screen.AdbScreen`).

**Input** is one control server for the session, started as it is attached and ended with it: a finger is sent as it
moves -- down, each move, up -- in the display's pixels, about ten milliseconds a touch; a button or a key as its
Android key, Command held as Control. **Text** of printable ASCII is typed as it is; anything else -- accents, emoji,
other scripts, a line break -- is put on the clipboard and pasted, so it types whole.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable

from sim_mirror.connectors.android import scrcpy
from sim_mirror.connectors.android.geometry import Rotation, on_display
from sim_mirror.connectors.android.keys import BUTTON_KEYCODES, MODIFIERS, keycode_of
from sim_mirror.connectors.android.screen import AdbScreen
from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.core.frames import is_sync_point

#: How often a video stream checks whether its frames have gone too long without a key frame.
KEY_FRAME_CHECK_S = 0.2
#: How many frames may come without a key frame: fewer than a joining viewer is handed (`frames.QUEUE_MAX`).
KEYLESS_MAX = 100
#: The least time between two requests to capture again.
RESET_GAP_S = 10.0


class ScrcpyScreen:
    """An Android screen streamed live by scrcpy, and read a screenshot at a time by adb."""

    def __init__(
        self,
        shots: AdbScreen,
        launcher: scrcpy.ScrcpyLauncher,
        serial: str,
        version: str,
        *,
        on_resized: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._shots = shots
        self._launcher = launcher
        self._serial = serial
        self._version = version
        self._on_resized = on_resized
        self._clock = clock

    async def describe(self) -> Screen:
        return await self._shots.describe()

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        return await self._shots.screenshot(max_width=max_width, quality=quality, crop=crop)

    async def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        screen = await self._shots.describe()
        options = scrcpy.video_options(
            max_size=round(max(screen.width_px, screen.height_px) * scale),
            fps=fps,
            bitrate=bitrate,
            key_frame_s=key_frame_s,
        )
        server = await self._launcher.start(self._serial, self._version, {**options, "control": "true"}, sockets=2)
        #: When the last key frame came, and how many frames have come since.
        last_key, since_key = [self._clock()], [0]
        reset = asyncio.get_running_loop().create_task(self._key_frames(server, last_key, since_key))
        units = scrcpy.h264_units(server.reader)
        try:
            # Its frames come until it fails or the stream is let go: it never simply ends.
            while True:
                unit, _width, _height = await anext(units)
                if is_sync_point(unit):
                    last_key[0], since_key[0] = self._clock(), 0
                else:
                    since_key[0] += 1
                yield unit
        except scrcpy.SizeChanged:
            self._on_resized()
            raise
        finally:
            reset.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reset
            await server.close()

    async def _key_frames(self, server: scrcpy.ScrcpyServer, last_key: list[float], since_key: list[int]) -> None:
        """Ask for a key frame once `KEYLESS_MAX` frames have come without one, and not again for `RESET_GAP_S` --
        never of a still screen."""
        _reader, control = server.sockets[1]
        while True:
            await asyncio.sleep(KEY_FRAME_CHECK_S)
            if since_key[0] >= KEYLESS_MAX and self._clock() - last_key[0] >= RESET_GAP_S:
                control.write(scrcpy.reset_video())
                with contextlib.suppress(ConnectionError):
                    await control.drain()
                last_key[0] = self._clock()


class ScrcpyControl:
    """A session's control server: what its input and its text write to."""

    def __init__(self, server: scrcpy.ScrcpyServer) -> None:
        self.server = server
        self._lock = asyncio.Lock()
        self._drain = asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        """What the device sends back -- its clipboard, when it changes -- read and let go of, so it never fills."""
        with contextlib.suppress(Exception):
            while await self.server.reader.read(4096):
                pass

    async def send(self, *messages: bytes) -> None:
        async with self._lock:
            try:
                self.server.writer.write(b"".join(messages))
                await self.server.writer.drain()
            except (ConnectionError, RuntimeError) as exc:
                raise ConnectorError("scrcpy's control socket closed: the device is attached again") from exc

    @property
    def running(self) -> bool:
        return self.server.running

    async def close(self) -> None:
        self._drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain
        await self.server.close()


class ScrcpyInput:
    """Touches, buttons and keys through scrcpy, sent as they come."""

    def __init__(self, control: ScrcpyControl, screen: Screen, rotation: Rotation) -> None:
        self._control = control
        self._screen = screen
        self._rotation = rotation

    async def _display(self) -> tuple[int, int, int]:
        quarters = await self._rotation.quarters()
        width, height = self._screen.width_px, self._screen.height_px
        return (quarters, height, width) if quarters % 2 else (quarters, width, height)

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        down = False
        held: set[int] = set()
        async for event in events:
            if event.kind == "touch":
                quarters, width, height = await self._display()
                x, y = on_display(self._screen, quarters, event.x, event.y)
                action = scrcpy.ACTION_UP if event.phase == "up" else scrcpy.ACTION_MOVE if down else scrcpy.ACTION_DOWN
                down = event.phase != "up"
                await self._control.send(scrcpy.touch(action, x, y, width, height))
            elif event.kind == "button":
                code = BUTTON_KEYCODES.get(event.button)
                if code is None:
                    raise ConnectorError(f"an Android device has no {event.button} button")
                await self._control.send(scrcpy.key(_action(event), code))
            elif event.code in MODIFIERS:
                (held.add if event.phase == "down" else held.discard)(event.code)
            else:
                code = keycode_of(event.code)
                if code is None:
                    raise ConnectorError(f"key {event.code} has no Android key")
                meta = 0
                for modifier in held:
                    meta |= MODIFIERS[modifier][1]
                await self._control.send(scrcpy.key(_action(event), code, meta))


def _action(event: HidEvent) -> int:
    return scrcpy.ACTION_DOWN if event.phase == "down" else scrcpy.ACTION_UP


class ScrcpyText:
    """Text typed whole through scrcpy: printable ASCII as it is, anything else pasted from the clipboard."""

    def __init__(self, control: ScrcpyControl) -> None:
        self._control = control
        self._sequence = itertools.count(1)

    async def type(self, text: str) -> None:
        if text.isascii() and text.isprintable():
            chunks = [text[start : start + scrcpy.TEXT_MAX] for start in range(0, len(text), scrcpy.TEXT_MAX)]
            await self._control.send(*(scrcpy.text(chunk) for chunk in chunks))
            return
        await self._control.send(scrcpy.clipboard(next(self._sequence), text, paste=True))
