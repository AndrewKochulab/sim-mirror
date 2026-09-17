# SPDX-License-Identifier: Apache-2.0
"""A picture with text SimMirror knows, to check that the text reader reads (`sim-mirror doctor`)."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

#: What the picture says.
PROBE_TEXT = "Tap to continue"
#: How big the letters are, in pixels: a phone's body text, at two pixels a point.
PROBE_SIZE = 40


def probe_picture(text: str = PROBE_TEXT) -> bytes:
    """A JPEG of dark text on a light screen, as an app might show it."""
    font = ImageFont.load_default(size=PROBE_SIZE)
    left, top, right, bottom = font.getbbox(text)
    image = Image.new("L", (int(right - left) + 4 * PROBE_SIZE, int(bottom - top) + 4 * PROBE_SIZE), 250)
    ImageDraw.Draw(image).text((2 * PROBE_SIZE - left, 2 * PROBE_SIZE - top), text, fill=20, font=font)
    out = BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()
