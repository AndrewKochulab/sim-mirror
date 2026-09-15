# SPDX-License-Identifier: Apache-2.0
"""Knowing when the screen has stopped moving: an animation is over.

A `SettleDetector` answers once the screen has not changed for a while, or says it is still changing. The one v1 ships,
`ScreenshotSettle`, hashes small screenshots of what it watches, taken `POLL_S` apart.

A focused field's caret fades in and out for as long as the field has focus -- through several pictures, measured on a
device -- which held every wait on a screen with a field being typed into to its timeout. So while a field is editing,
its own row is not watched: the screen above and below it must hold still. Anything else still moving -- a spinner,
the keyboard sliding in -- still waits.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Protocol

from sim_mirror.connectors.base import ConnectorError, Crop, Screen, ScreenSource
from sim_mirror.perception.snapshot import Snapshot

POLL_S = 0.15
#: How wide the screenshots compared are: enough to see motion, small enough to be cheap.
SETTLE_WIDTH = 160
SETTLE_QUALITY = 40
#: How far above and below a focused field's middle its row reaches: the band a settle wait does not watch.
FIELD_HALF_PT = 30.0
LEFT_OUT = " (the focused field's row left out)"


class SettleDetector(Protocol):
    async def settle(self, quiet_s: float, timeout_s: float) -> str:
        """Wait until the screen has held still for `quiet_s`, at most `timeout_s`, and say how that went."""
        ...


class ScreenshotSettle:
    def __init__(
        self,
        *,
        read: Callable[[], Awaitable[Snapshot]],
        source: ScreenSource,
        screen: Screen,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        unreadable: tuple[type[BaseException], ...],
    ) -> None:
        self._read = read
        self._source = source
        self._screen = screen
        self._clock = clock
        self._sleep = sleep
        self._unreadable = unreadable

    async def _watched(self) -> tuple[list[Crop | None], bool]:
        """What is watched: the whole screen, or -- while a field is editing -- the screen above and below that field's
        row; and whether a field was left out. A screen that cannot be read is watched whole."""
        try:
            snapshot = await self._read()
        except self._unreadable:
            return [None], False
        editing = next((element for element in snapshot.elements if "editing" in element.flags), None)
        if editing is None:
            return [None], False
        width, height = float(self._screen.width_pt), float(self._screen.height_pt)
        top, bottom = max(0.0, editing.y - FIELD_HALF_PT), min(height, editing.y + FIELD_HALF_PT)
        bands: list[Crop | None] = [Crop(0.0, 0.0, width, top)] if top > 0 else []
        if bottom < height:
            bands.append(Crop(0.0, bottom, width, height - bottom))
        return bands, True

    async def settle(self, quiet_s: float, timeout_s: float) -> str:
        started = self._clock()
        bands, left_out = await self._watched()
        said = LEFT_OUT if left_out else ""
        last, still_since = b"", started
        while True:
            watched = hashlib.blake2b(digest_size=8)
            try:
                for crop in bands:
                    shot = await self._source.screenshot(max_width=SETTLE_WIDTH, quality=SETTLE_QUALITY, crop=crop)
                    watched.update(shot.jpeg)
            except ConnectorError as exc:
                return f"could not watch the screen settle: {exc}"
            now = self._clock()
            digest = watched.digest()
            if digest != last:
                last, still_since = digest, now
            elif now - still_since >= quiet_s:
                return f"settled after {round((still_since - started) * 1000)}ms{said}"
            if now - started >= timeout_s:
                return f"still changing after {round((now - started) * 1000)}ms"
            await self._sleep(POLL_S)
