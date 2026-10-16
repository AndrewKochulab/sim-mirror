# SPDX-License-Identifier: Apache-2.0
"""Knowing when the screen has stopped moving: an animation is over.

A `SettleDetector` answers once the screen has not changed for a while, or says it is still changing. `ScreenshotSettle`
looks at small screenshots taken `POLL_S` apart, and a `Stillness` says whether each look is still the screen the quiet
window began with -- the two ways of seeing are the scope's ``perception.settle``:

* `PerceptualStillness` (``perceptual``, the default) compares a coarse grid of the screen's brightness
  (`perception.imagehash`) with the look that began the quiet window, so a slow fade adds up to a change and never
  passes as still. A few small places that keep changing look after look -- a spinner, a pulsing dot, a shimmer, a
  caret -- are found and no longer watched: an animation that never quite stops does not hold a wait to its timeout,
  while a label that changes once, or a row that appears, still starts the window again. Its limit: text inside a place
  that kept moving -- an animated ellipsis -- is not watched either; wait for the text instead.
* `ExactStillness` (``exact``, SimMirror 1.0's) waits until not one byte of a screenshot changes.

A focused field's caret fades in and out for as long as the field has focus -- through several pictures, measured on a
device -- which held every exact wait on a screen with a field being typed into to its timeout. So while a field is
editing, its own row is not watched: the screen above and below it must hold still. Exact stillness asks the connector
for those two parts; perceptual stillness paints the row out of one whole screenshot, which every connector can take.
The row is the app's, across the app as it is drawn: in an app turned to landscape it is a strip down the device's
portrait screen (`perception.orientation`), and the parts watched are beside it.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Crop, Screen, ScreenSource, Shot
from sim_mirror.perception.imagehash import LEVEL, Grid, ImageUnreadable, grid_of, moved_cells
from sim_mirror.perception.orientation import PORTRAIT, Interface, Orientation
from sim_mirror.perception.snapshot import Snapshot

POLL_S = 0.15
#: How wide the screenshots compared are: enough to see motion, small enough to be cheap.
SETTLE_WIDTH = 160
SETTLE_QUALITY = 40
#: How far above and below a focused field's middle its row reaches, in the app's points: the band a settle wait does
#: not watch.
FIELD_HALF_PT = 30.0
LEFT_OUT = "the focused field's row left out"
#: How many looks a cell must change in, with no change across the screen between, to be taken for an animation.
RESTLESS_CHANGES = 3
#: The share of a grid's cells that, moving together, is the screen changing -- a push, a scroll -- not an animation.
RESTLESS_SHARE = 0.125


class SettleDetector(Protocol):
    async def settle(self, quiet_s: float, timeout_s: float) -> str:
        """Wait until the screen has held still for `quiet_s`, at most `timeout_s`, and say how that went."""
        ...


class Stillness(Protocol):
    """What counts as the screen holding still, over one settle wait's looks."""

    def crops(self, screen: Screen, left_out: Crop | None) -> list[Crop | None]:
        """The screenshots each look takes: None for the whole screen. `left_out` is the band of the device's screen,
        in its points, not watched."""
        ...

    def look(self, shots: Sequence[Shot], screen: Screen, left_out: Crop | None) -> bool:
        """Whether this look is still the screen the quiet window began with; one that is not begins a new window.
        Raises `ImageUnreadable` for a screenshot it cannot see into."""
        ...

    def notes(self) -> list[str]:
        """What the answer should add about what was not watched."""
        ...


class ExactStillness:
    """Still while every byte of the screenshots is: SimMirror 1.0's settling."""

    def __init__(self) -> None:
        self._last = b""

    def crops(self, screen: Screen, left_out: Crop | None) -> list[Crop | None]:
        """The screen around the band left out: above and below a band across it, beside one down it."""
        if left_out is None:
            return [None]
        width, height = float(screen.width_pt), float(screen.height_pt)
        left, top = left_out.x, left_out.y
        right, bottom = left + left_out.width, top + left_out.height
        parts = [
            Crop(0.0, 0.0, width, top),
            Crop(0.0, bottom, width, height - bottom),
            Crop(0.0, top, left, bottom - top),
            Crop(right, top, width - right, bottom - top),
        ]
        return [part for part in parts if part.width > 0 and part.height > 0]

    def look(self, shots: Sequence[Shot], screen: Screen, left_out: Crop | None) -> bool:
        watched = hashlib.blake2b(digest_size=8)
        for shot in shots:
            watched.update(shot.jpeg)
        digest = watched.digest()
        still, self._last = digest == self._last, digest
        return still

    def notes(self) -> list[str]:
        return []


class PerceptualStillness:
    """Still while the grid of the screen's brightness is, but for a few cells, the one the quiet window began with --
    cells found to keep changing left out."""

    def __init__(self, *, columns: int, tolerance: int, level: int = LEVEL) -> None:
        self._columns = columns
        self._tolerance = tolerance
        self._level = level
        self._anchor: Grid | None = None
        self._previous: Grid | None = None
        self._changes: Counter[int] = Counter()
        self._restless: set[int] = set()

    def crops(self, screen: Screen, left_out: Crop | None) -> list[Crop | None]:
        return [None]

    def look(self, shots: Sequence[Shot], screen: Screen, left_out: Crop | None) -> bool:
        bands = () if left_out is None else (_shares(left_out, screen),)
        grid = grid_of(shots[0].jpeg, columns=self._columns, left_out=bands)
        previous, anchor, self._previous = self._previous, self._anchor, grid
        if previous is None or anchor is None:
            self._anchor = grid
            return False
        self._count(moved_cells(previous, grid, level=self._level), len(grid.cells))
        if len(moved_cells(anchor, grid, level=self._level) - self._restless) > self._tolerance:
            self._anchor = grid
            return False
        return True

    def _count(self, stepped: frozenset[int], cells: int) -> None:
        """Count the cells that changed since the last look; a change across the screen starts the count again."""
        if len(stepped) > RESTLESS_SHARE * cells:
            self._changes.clear()
            self._restless.clear()
            return
        self._changes.update(stepped)
        self._restless.update(cell for cell in stepped if self._changes[cell] >= RESTLESS_CHANGES)

    def notes(self) -> list[str]:
        places = len(self._restless)
        if places == 0:
            return []
        if places == 1:
            return ["1 small place kept moving and was not watched"]
        return [f"{places} small places kept moving and were not watched"]


def _shares(region: Crop, screen: Screen) -> tuple[float, float, float, float]:
    """A region of the screen as shares of its width and height: left, top, right and bottom."""
    width, height = float(screen.width_pt), float(screen.height_pt)
    return (
        region.x / width,
        region.y / height,
        (region.x + region.width) / width,
        (region.y + region.height) / height,
    )


def stillness_for(config: SimConfig) -> Stillness:
    """The stillness a scope's ``perception.settle`` asks for, fresh for one wait."""
    if config.settle_mode == "exact":
        return ExactStillness()
    return PerceptualStillness(columns=config.settle_grid, tolerance=config.settle_tolerance)


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
        stillness: Stillness | None = None,
        orientation: Callable[[], Orientation] | None = None,
    ) -> None:
        """`screen` is the device's; `orientation` says which way the app was turned on it when `read` last read it,
        portrait when not given."""
        self._read = read
        self._source = source
        self._screen = screen
        self._clock = clock
        self._sleep = sleep
        self._unreadable = unreadable
        self._stillness = stillness or ExactStillness()
        self._orientation = orientation or _portrait

    async def _left_out(self) -> Crop | None:
        """The row of a field being typed into, across the app, as the band of the device's screen it is on -- not
        watched; None when no field is editing, or the screen cannot be read."""
        try:
            snapshot = await self._read()
        except self._unreadable:
            return None
        editing = next((element for element in snapshot.elements if "editing" in element.flags), None)
        if editing is None:
            return None
        interface = Interface(self._screen, self._orientation())
        across, down = float(interface.screen.width_pt), float(interface.screen.height_pt)
        top, bottom = max(0.0, editing.y - FIELD_HALF_PT), min(down, editing.y + FIELD_HALF_PT)
        return interface.crop(Crop(0.0, top, across, bottom - top))

    def _said(self, left_out: Crop | None) -> str:
        notes = ([LEFT_OUT] if left_out is not None else []) + self._stillness.notes()
        return f" ({'; '.join(notes)})" if notes else ""

    async def settle(self, quiet_s: float, timeout_s: float) -> str:
        started = self._clock()
        left_out = await self._left_out()
        crops = self._stillness.crops(self._screen, left_out)
        still_since = started
        while True:
            try:
                shots = [
                    await self._source.screenshot(max_width=SETTLE_WIDTH, quality=SETTLE_QUALITY, crop=crop)
                    for crop in crops
                ]
                still = self._stillness.look(shots, self._screen, left_out)
            except (ConnectorError, ImageUnreadable) as exc:
                return f"could not watch the screen settle: {exc}"
            now = self._clock()
            if not still:
                still_since = now
            elif now - still_since >= quiet_s:
                return f"settled after {round((still_since - started) * 1000)}ms{self._said(left_out)}"
            if now - started >= timeout_s:
                return f"still changing after {round((now - started) * 1000)}ms"
            await self._sleep(POLL_S)


def _portrait() -> Orientation:
    return PORTRAIT
