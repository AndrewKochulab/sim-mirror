# SPDX-License-Identifier: Apache-2.0
"""A screenshot's pixels as a grid of brightness: its shape, what counts as moved, and what cannot be read."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from sim_mirror.perception.imagehash import LEVEL, Grid, ImageUnreadable, grid_of, moved_cells, rows_for
from sim_mirror.testing.fakes import tiny_jpeg
from sim_mirror.testing.pictures import HEIGHT, WIDTH, picture


@pytest.mark.parametrize(
    ("columns", "width", "height", "rows"),
    [(32, 160, 348, 70), (32, 348, 160, 15), (8, 400, 40, 8), (64, 40, 400, 128), (16, 0, 10, 128)],
)
def test_a_grid_keeps_its_cells_square_within_its_bounds(columns: int, width: int, height: int, rows: int) -> None:
    assert rows_for(columns, width, height) == rows


def test_a_screenshot_is_read_as_the_mean_brightness_of_each_cell() -> None:
    grid = grid_of(picture(boxes=[(0, 0, WIDTH, HEIGHT // 2, 0)]), columns=32)
    assert (grid.columns, grid.rows, len(grid.cells)) == (32, 70, 32 * 70)
    top, bottom = grid.cells[: 32 * 30], grid.cells[32 * 40 :]
    assert max(top) < LEVEL and min(bottom) > 255 - LEVEL


def test_the_same_screen_encoded_twice_has_not_moved() -> None:
    boxes = [(20, 40, 120, 14, 30)]
    crisp, rough = picture(boxes=boxes), picture(boxes=boxes, quality=40)
    assert moved_cells(grid_of(crisp, columns=32), grid_of(rough, columns=32)) == frozenset()


def test_a_label_that_changes_moves_the_cells_it_covers_and_only_those() -> None:
    before = grid_of(picture(boxes=[(20, 100, 40, 10, 0)]), columns=32)
    after = grid_of(picture(boxes=[(20, 100, 80, 10, 0)]), columns=32)
    moved = moved_cells(before, after)
    # Each cell is 5 pixels square: the label grew over pixels 60-99 of rows 100-109.
    assert moved and all(19 <= index // 32 <= 22 and 11 <= index % 32 <= 20 for index in moved)


def test_a_change_only_just_past_the_level_counts() -> None:
    grid = Grid(2, 1, bytes([100, 100]))
    assert moved_cells(grid, Grid(2, 1, bytes([100 + LEVEL, 100 - LEVEL]))) == frozenset()
    assert moved_cells(grid, Grid(2, 1, bytes([100 + LEVEL + 1, 100]))) == frozenset({0})
    assert moved_cells(grid, Grid(2, 1, bytes([100, 90])), level=5) == frozenset({1})


def test_grids_of_different_shapes_have_moved_everywhere() -> None:
    assert moved_cells(Grid(2, 1, bytes(2)), Grid(1, 3, bytes(3))) == frozenset({0, 1, 2})


def test_a_band_left_out_reads_the_same_whatever_is_in_it() -> None:
    caret_on, caret_off = picture(boxes=[(10, 60, 2, 20, 0)]), picture()
    band = [(50 / HEIGHT, 90 / HEIGHT)]
    assert moved_cells(grid_of(caret_on, columns=32), grid_of(caret_off, columns=32))
    left_out = grid_of(caret_on, columns=32, left_out=band), grid_of(caret_off, columns=32, left_out=band)
    assert moved_cells(*left_out) == frozenset()


@pytest.mark.parametrize("data", [b"", b"not a picture", tiny_jpeg(402, 874)])
def test_what_is_not_a_readable_jpeg_is_refused(data: bytes) -> None:
    with pytest.raises(ImageUnreadable, match="the screenshot could not be decoded"):
        grid_of(data, columns=32)


def test_a_picture_in_another_format_is_refused() -> None:
    png = BytesIO()
    Image.new("L", (10, 10)).save(png, format="PNG")
    with pytest.raises(ImageUnreadable):
        grid_of(png.getvalue(), columns=8)
