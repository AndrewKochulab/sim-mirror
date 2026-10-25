# SPDX-License-Identifier: Apache-2.0
"""Touches, keys and text through adb's ``input`` command: what an Android device takes before scrcpy is set up.

Each ``input`` starts a program on the device, a few hundred milliseconds apiece, so a finger is not sent move by move:
its stroke is kept from down to up, as WebDriverAgent's is (`connectors.iphone.wda_roles`), and sent whole -- a tap
where it barely moved and soon lifted, else a swipe from where it went down to where it lifted, taking as long as it
did (a long press is a swipe that stays put). A curve is sent straight; scrcpy's input keeps it.

A key is sent as its Android keycode, with Command held as Control (select all, paste); a button as its key. Text is
typed with ``input text``, which takes printable ASCII only -- anything else is refused, saying scrcpy types it.
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterable, Callable, Sequence

from sim_mirror.connectors.android.geometry import Rotation, on_display
from sim_mirror.connectors.android.keys import BUTTON_KEYCODES, MODIFIERS, keycode_of
from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.platform.adb import Adb, AdbError

#: A stroke that moved less than this, in points, and lifted sooner than `TAP_MAX_S`, is a tap.
TAP_SLOP_PT = 8.0
TAP_MAX_S = 0.4
#: The shortest swipe ``input`` is asked for, in milliseconds.
SWIPE_MIN_MS = 50
#: How long typing may take, in seconds, per character and at least.
TYPE_S_PER_CHAR = 0.05
TYPE_MIN_S = 10.0


def stroke_command(screen: Screen, quarters: int, stroke: Sequence[tuple[float, float, float]]) -> list[str]:
    """The ``input`` command a finger's stroke -- its points, each with when it was reached -- is sent as."""
    (start, x0, y0), (end, x1, y1) = stroke[0], stroke[-1]
    took = end - start
    a, b = on_display(screen, quarters, x0, y0), on_display(screen, quarters, x1, y1)
    if math.dist((x0, y0), (x1, y1)) < TAP_SLOP_PT and took < TAP_MAX_S:
        return ["input", "tap", str(a[0]), str(a[1])]
    return ["input", "swipe", *map(str, (*a, *b)), str(max(SWIPE_MIN_MS, round(took * 1000)))]


class AdbInput:
    """An Android device's input, through ``input``."""

    def __init__(
        self,
        adb: Adb,
        serial: str,
        screen: Screen,
        rotation: Rotation,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adb = adb
        self._serial = serial
        self._screen = screen
        self._rotation = rotation
        self._clock = clock

    async def _input(self, command: Sequence[str]) -> None:
        try:
            await self._adb.run(self._serial, command)
        except AdbError as exc:
            raise ConnectorError(f"the device did not take the input: {exc}") from exc

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        stroke: list[tuple[float, float, float]] = []
        held: set[int] = set()
        async for event in events:
            if event.kind == "touch":
                stroke.append((self._clock(), event.x, event.y))
                if event.phase == "up":
                    await self._input(stroke_command(self._screen, await self._rotation.quarters(), stroke))
                    stroke = []
            elif event.kind == "button":
                if event.phase == "down":
                    await self._press(event.button)
            elif event.code in MODIFIERS:
                (held.add if event.phase == "down" else held.discard)(event.code)
            elif event.phase == "down":
                await self._key(event.code, held)

    async def _press(self, button: str) -> None:
        code = BUTTON_KEYCODES.get(button)
        if code is None:
            raise ConnectorError(f"an Android device has no {button} button")
        await self._input(["input", "keyevent", str(code)])

    async def _key(self, usage: int, held: set[int]) -> None:
        code = keycode_of(usage)
        if code is None:
            raise ConnectorError(f"key {usage} has no Android key")
        if held:
            await self._input(["input", "keycombination", *(str(MODIFIERS[key][0]) for key in sorted(held)), str(code)])
        else:
            await self._input(["input", "keyevent", str(code)])


def typed_argument(text: str) -> str:
    """Text as ``input text`` takes it: a space is ``%s`` to it."""
    if not text.isascii() or not text.isprintable():
        raise ConnectorError(
            "Without scrcpy, only plain ASCII text can be typed: set scrcpy up (Set up touch, or sim-mirror android "
            "setup) to type any text"
        )
    return text.replace(" ", "%s")


class AdbText:
    """Text typed whole through ``input text``: printable ASCII."""

    def __init__(self, adb: Adb, serial: str) -> None:
        self._adb = adb
        self._serial = serial

    async def type(self, text: str) -> None:
        argument = typed_argument(text)
        try:
            await self._adb.run(
                self._serial, ["input", "text", argument], timeout=max(TYPE_MIN_S, len(text) * TYPE_S_PER_CHAR)
            )
        except AdbError as exc:
            raise ConnectorError(f"the device did not take the text: {exc}") from exc
