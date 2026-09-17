# SPDX-License-Identifier: Apache-2.0
"""Real JPEGs of made-up screens, for tests that look at pixels rather than at bytes.

`picture` paints a background and a few boxes -- a label, a row, a spinner's arm -- and encodes the result as a
settle screenshot would be. `PictureEngine` is a `FakeEngine` whose screenshots are a script of such pictures, one per
look, the last one held.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from sim_mirror.connectors.base import Crop, Shot
from sim_mirror.testing.fakes import FakeEngine

#: A box painted on a picture: left, top, width and height in pixels, and its brightness from 0 to 255.
Box = tuple[int, int, int, int, int]
#: A settle screenshot's size: 160 pixels across a 402x874-point phone.
WIDTH, HEIGHT = 160, 348


def picture(
    width: int = WIDTH, height: int = HEIGHT, *, background: int = 255, boxes: Sequence[Box] = (), quality: int = 90
) -> bytes:
    """A grey JPEG of this size with these boxes painted on it."""
    image = Image.new("L", (width, height), background)
    draw = ImageDraw.Draw(image)
    for left, top, box_width, box_height, shade in boxes:
        draw.rectangle((left, top, left + box_width - 1, top + box_height - 1), fill=shade)
    out = BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


class PictureEngine(FakeEngine):
    """A device whose screenshots are `pictures` in turn: ``pictures(look)`` for the look counted from 0."""

    def __init__(
        self, pictures: Callable[[int], bytes] | Sequence[bytes], *, document: dict[str, Any] | None = None
    ) -> None:
        super().__init__(document=document)
        self._pictures = pictures
        self.looks = 0

    def _picture(self, look: int) -> bytes:
        if callable(self._pictures):
            return self._pictures(look)
        return self._pictures[min(look, len(self._pictures) - 1)]

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality, crop=crop)
        jpeg = self._picture(self.looks)
        self.looks += 1
        return Shot(jpeg, WIDTH, HEIGHT)
