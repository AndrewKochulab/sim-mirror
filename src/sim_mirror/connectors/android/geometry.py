# SPDX-License-Identifier: Apache-2.0
"""An Android screen's size, and which way it is turned, as SimMirror's portrait points want them.

**Size**: ``wm size`` and ``wm density`` say the display's pixels and its density, an override first where a person set
one. Its points are Android's density-independent pixels -- pixels × 160 / density -- whole, as the protocol has them:
a 1080 × 2424 Pixel 9 at 420 dpi is 411 × 923.

**Turning**: SimMirror's screen is the device's own portrait one, whichever way an app is turned (`connectors.base`).
Android turns its whole display instead -- measured on an API 36 emulator, a screenshot, the UI tree's bounds and the
coordinates a touch is injected at are all the turned display's, 2424 × 1080 once turned -- and says by how many
quarters (``mCurrentOrientation`` in ``dumpsys display``, ``rotation`` in a UI dump). Turned one quarter, the
interface's top left is the portrait screen's top right: UIKit's landscape right (`perception.orientation`), and three
quarters is landscape left. So a screenshot is turned back to portrait, a touch on the portrait screen is turned onto
the display, and the UI tree is read as an interface turned that way, which `perception.orientation` turns for agents.
"""

from __future__ import annotations

import io
import re
import time
from collections.abc import Awaitable, Callable

from PIL import Image

from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.perception.orientation import Interface, Orientation

#: Each quarter turn of an Android display, as the way an interface lies on the portrait screen.
ROTATIONS: tuple[Orientation, ...] = ("portrait", "landscape_right", "portrait_upside_down", "landscape_left")
#: How a picture of the turned display is turned back to the portrait screen: Pillow turns anticlockwise.
_TO_PORTRAIT = {
    1: Image.Transpose.ROTATE_270,
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}
#: Android's baseline density: a point is a pixel at 160 dpi.
BASELINE_DPI = 160
#: How long which way the display is turned is trusted before it is asked again.
ROTATION_TTL_S = 1.0
_SIZE = re.compile(r"(Physical|Override) size: (\d+)x(\d+)")
_DENSITY = re.compile(r"(Physical|Override) density: (\d+)")
_CURRENT = re.compile(r"mCurrentOrientation=(\d)")


def screen_of(size: str, density: str) -> Screen:
    """The portrait screen ``wm size`` and ``wm density`` describe, an override first."""
    sizes = {kind: (int(w), int(h)) for kind, w, h in _SIZE.findall(size)}
    densities = {kind: int(dpi) for kind, dpi in _DENSITY.findall(density)}
    pixels = sizes.get("Override") or sizes.get("Physical")
    dpi = densities.get("Override") or densities.get("Physical")
    if pixels is None or not dpi:
        raise ConnectorError(f"the device did not say its screen's size: {size.strip() or density.strip()!r}")
    width, height = sorted(pixels)
    scale = dpi / BASELINE_DPI
    return Screen(width, height, round(width / scale), round(height / scale), scale)


def orientation_of(quarters: int) -> Orientation:
    return ROTATIONS[quarters % 4]


def quarters_of(dumpsys_display: str) -> int:
    """How many quarters the display is turned, from ``dumpsys display``; 0 when it does not say."""
    found = _CURRENT.search(dumpsys_display)
    return int(found.group(1)) % 4 if found else 0


def on_display(screen: Screen, quarters: int, x: float, y: float) -> tuple[int, int]:
    """A point of the portrait screen, as the turned display's pixels a touch is injected at."""
    ix, iy = Interface(screen, orientation_of(quarters)).on_interface(x, y)
    return round(ix * screen.scale), round(iy * screen.scale)


def portrait_jpeg(
    jpeg: bytes, quarters: int, *, max_width: int, quality: int, crop: tuple[int, int, int, int] | None
) -> tuple[bytes, int, int]:
    """A screenshot of the turned display, turned back to the portrait screen, cropped to a box of its pixels, and
    shrunk to `max_width`: the JPEG, its width and height."""
    try:
        opened = Image.open(io.BytesIO(jpeg))
        opened.load()
        image: Image.Image = opened
    except (OSError, ValueError) as exc:
        raise ConnectorError("the device's screenshot is not a picture") from exc
    turn = _TO_PORTRAIT.get(quarters % 4)
    if turn is not None:
        image = image.transpose(turn)
    if crop is not None:
        image = image.crop(crop)
    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue(), image.width, image.height


class Rotation:
    """Which way the display is turned, asked at most every `ROTATION_TTL_S`."""

    def __init__(self, ask: Callable[[], Awaitable[str]], clock: Callable[[], float] = time.monotonic) -> None:
        self._ask = ask
        self._clock = clock
        self._known: tuple[float, int] | None = None

    async def quarters(self) -> int:
        now = self._clock()
        if self._known is None or now - self._known[0] >= ROTATION_TTL_S:
            self._known = (now, quarters_of(await self._ask()))
        return self._known[1]

    def forget(self) -> None:
        """The display was just turned: ask again next time."""
        self._known = None
