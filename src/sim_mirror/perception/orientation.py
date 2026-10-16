# SPDX-License-Identifier: Apache-2.0
"""Which way an app's interface is turned on a device's screen, and the screen as an agent sees it then.

A device's screen does not turn. A simulator's framebuffer, its touches and the size `ScreenSource.describe` answers are
the screen's own portrait points whichever way an app is drawn on it; an app turned to landscape is drawn sideways on
that portrait screen. Accessibility answers in the interface's points instead: on an iPhone 17 Pro turned landscape
right its tree is 874 by 402 points, on a screen 402 by 874.

A reader's document says which way its interface is turned (``orientation``, one of `ORIENTATIONS`; the native helper
finds it, and the idb connector the same way, `connectors.idb.orientation`), and an agent works in that interface:
its snapshots, the points it gives, its screenshots and its regions are the interface's, upright. `Interface` is the
one place the two meet: what an agent's points are on the screen, its regions, its touches, and its screenshots turned
upright. A person in the viewer sees and touches the screen itself, so nothing there is turned.

The four ways are UIKit's. Measured on an iPhone 17 Pro simulator: an app turned landscape right is drawn with its top
along the screen's right edge -- the screen's top right corner is the interface's top left -- and one turned landscape
left with its top along the left edge. Turning the device itself, as Simulator.app's Device > Rotate does, turns the
screen no more: measured on iOS 26.5, an iPhone turned either way and an iPad upside down kept a portrait framebuffer
with the app drawn turned in it (`docs/screen-understanding.md`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Literal, get_args

from PIL import Image

from sim_mirror.connectors.base import Crop, HidEvent, Screen, Shot

Orientation = Literal["portrait", "portrait_upside_down", "landscape_left", "landscape_right"]
ORIENTATIONS: tuple[Orientation, ...] = get_args(Orientation)
PORTRAIT: Orientation = "portrait"
SIDEWAYS: frozenset[Orientation] = frozenset({"landscape_left", "landscape_right"})
#: How a screenshot of the screen is turned to show the interface upright: Pillow turns anticlockwise.
_UPRIGHT = {
    "portrait_upside_down": Image.Transpose.ROTATE_180,
    "landscape_right": Image.Transpose.ROTATE_90,
    "landscape_left": Image.Transpose.ROTATE_270,
}
#: How a turned interface is said.
WORDS = {"portrait_upside_down": "upside down", "landscape_left": "landscape", "landscape_right": "landscape"}
#: The quality a screenshot turned upright is written at again.
UPRIGHT_QUALITY = 80


def orientation_of(raw: object) -> Orientation:
    """A document's ``orientation``; portrait when it says none, or one SimMirror does not know."""
    return next((known for known in ORIENTATIONS if known == raw), PORTRAIT)


@dataclass(frozen=True)
class Interface:
    """A device's screen as an app's interface lies on it: the screen, and the way the interface is turned."""

    device: Screen
    orientation: Orientation = PORTRAIT

    @property
    def turned(self) -> bool:
        return self.orientation != PORTRAIT

    @property
    def sideways(self) -> bool:
        return self.orientation in SIDEWAYS

    @property
    def screen(self) -> Screen:
        """The interface's size: the screen's, turned a quarter when the interface is sideways."""
        if not self.sideways:
            return self.device
        device = self.device
        return Screen(device.height_px, device.width_px, device.height_pt, device.width_pt, device.scale)

    def words(self) -> str:
        """How the interface lies, said: nothing when it is upright."""
        return WORDS.get(self.orientation, "")

    def on_device(self, x: float, y: float) -> tuple[float, float]:
        """Where a point of the interface is on the device's screen."""
        width, height = float(self.device.width_pt), float(self.device.height_pt)
        if self.orientation == "landscape_right":
            return width - y, x
        if self.orientation == "landscape_left":
            return y, height - x
        if self.orientation == "portrait_upside_down":
            return width - x, height - y
        return x, y

    def on_interface(self, x: float, y: float) -> tuple[float, float]:
        """Where a point of the device's screen is in the interface: `on_device` the other way."""
        width, height = float(self.device.width_pt), float(self.device.height_pt)
        if self.orientation == "landscape_right":
            return y, width - x
        if self.orientation == "landscape_left":
            return height - y, x
        if self.orientation == "portrait_upside_down":
            return width - x, height - y
        return x, y

    def crop(self, region: Crop) -> Crop:
        """A region of the interface as the region of the device's screen it covers."""
        corners = [
            self.on_device(region.x, region.y),
            self.on_device(region.x + region.width, region.y + region.height),
        ]
        return _spanned(corners)

    def region(self, crop: Crop) -> Crop:
        """A region of the device's screen as the region of the interface it covers: `crop` the other way."""
        far = self.on_interface(crop.x + crop.width, crop.y + crop.height)
        return _spanned([self.on_interface(crop.x, crop.y), far])

    def event(self, event: HidEvent) -> HidEvent:
        """An event at a point of the interface, at that point of the device's screen."""
        if event.kind != "touch" or not self.turned:
            return event
        x, y = self.on_device(event.x, event.y)
        return replace(event, x=x, y=y)

    async def events(self, events: AsyncIterable[HidEvent]) -> AsyncIterator[HidEvent]:
        """Events at points of the interface, as they come, at those points of the device's screen."""
        async for event in events:
            yield self.event(event)

    def device_width(self, width: int, region: Crop | None) -> int:
        """How wide to ask the device's screenshot for, so that turned upright it is no wider than `width`."""
        if not self.sideways:
            return width
        across, down = (
            (region.width, region.height) if region is not None else (self.device.width_pt, self.device.height_pt)
        )
        return max(1, int(width * across / down)) if across > 0 and down > 0 else width

    async def upright(self, shot: Shot, *, quality: int = UPRIGHT_QUALITY) -> Shot:
        """A screenshot of the device's screen turned so the interface on it is upright, written at `quality`. One
        that cannot be decoded is kept as it is."""
        turn = _UPRIGHT.get(self.orientation)
        if turn is None:
            return shot
        return await asyncio.to_thread(_turned, shot, turn, quality)


def _spanned(corners: list[tuple[float, float]]) -> Crop:
    """The region two opposite corners span, whichever way round they are."""
    left, right = sorted(corner[0] for corner in corners)
    top, bottom = sorted(corner[1] for corner in corners)
    return Crop(left, top, right - left, bottom - top)


def _turned(shot: Shot, turn: Image.Transpose, quality: int) -> Shot:
    try:
        with Image.open(BytesIO(shot.jpeg)) as image:
            upright = image.transpose(turn)
    except (OSError, ValueError, SyntaxError):
        return shot
    written = BytesIO()
    upright.convert("RGB").save(written, format="JPEG", quality=quality)
    return Shot(written.getvalue(), upright.width, upright.height)
