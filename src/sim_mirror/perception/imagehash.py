# SPDX-License-Identifier: Apache-2.0
"""A screenshot's pixels as a coarse grid of brightness: what a settle wait compares from one look to the next.

A perceptual fingerprint, not a byte hash: two JPEGs of a screen that did not change can differ in their bytes, and a
screen that did change by a few pixels is the same screen to anyone looking. `grid_of` decodes a screenshot, turns it
grey, and shrinks it to ``columns`` cells across -- each cell the mean brightness of the pixels it covers, about
12 points square on a phone at the default 32 columns. `moved_cells` says which cells of two grids differ by more than
``LEVEL``: enough to see text change or a row appear, not enough for JPEG noise or a faint shimmer to count.

A band of the screen can be left out -- a focused field's row, whose caret blinks for as long as it is focused -- by
painting it flat before shrinking, so it reads the same in every look.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

#: How far, of 255, a cell's brightness must move to count as moved: past JPEG noise at a settle screenshot's quality.
LEVEL = 16
#: The fewest and most rows a grid has, whatever the screen's shape.
ROWS = (8, 128)
#: How many pixels across and down each cell is decoded from: JPEG's own scaling brings a screenshot close to this
#: before the exact shrink, which is what makes a decode cheap.
DRAFT_SCALE = 4
#: The brightness a left-out band is painted with.
FLAT = 0


class ImageUnreadable(ValueError):
    """A screenshot that could not be decoded as a JPEG."""


@dataclass(frozen=True)
class Grid:
    """A screen's brightness, ``columns`` cells across and ``rows`` down, row by row, each 0 to 255."""

    columns: int
    rows: int
    cells: bytes


def rows_for(columns: int, width: int, height: int) -> int:
    """How many rows keep a grid's cells square on a picture of this size."""
    low, high = ROWS
    return max(low, min(high, round(columns * height / max(width, 1))))


def grid_of(jpeg: bytes, *, columns: int, left_out: Sequence[tuple[float, float]] = ()) -> Grid:
    """A screenshot as a grid ``columns`` cells across; `left_out` bands, each ``(top, bottom)`` as shares of the
    picture's height, are painted flat first. Raises `ImageUnreadable` for anything that is not a JPEG."""
    try:
        with Image.open(BytesIO(jpeg), formats=("JPEG",)) as image:
            rows = rows_for(columns, *image.size)
            image.draft("L", (columns * DRAFT_SCALE, rows * DRAFT_SCALE))
            grey = image.convert("L")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageUnreadable("the screenshot could not be decoded as a JPEG") from None
    if left_out:
        draw = ImageDraw.Draw(grey)
        for top, bottom in left_out:
            draw.rectangle((0, round(top * grey.height), grey.width, round(bottom * grey.height)), fill=FLAT)
    cells = grey.resize((columns, rows), Image.Resampling.BOX).tobytes()
    return Grid(columns, rows, cells)


def moved_cells(before: Grid, after: Grid, *, level: int = LEVEL) -> frozenset[int]:
    """The cells whose brightness moved by more than `level`; every cell when the grids are not the same shape, as
    when the device turned."""
    if (before.columns, before.rows) != (after.columns, after.rows):
        return frozenset(range(after.columns * after.rows))
    pairs = enumerate(zip(before.cells, after.cells, strict=True))
    return frozenset(index for index, (was, now) in pairs if abs(was - now) > level)
