# SPDX-License-Identifier: Apache-2.0
"""Which way the interface idb_companion reads is turned, found from the root's shape and what is at points."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from sim_mirror.connectors.base import Screen
from sim_mirror.connectors.idb.orientation import Hit, candidates, find, frame_of, leaves
from sim_mirror.perception.model import Frame
from sim_mirror.perception.orientation import Interface, Orientation
from sim_mirror.testing.fakes import SCREEN

LANDSCAPE = Frame(0, 0, 874, 402)
UPRIGHT = Frame(0, 0, 402, 874)


def node(frame: Frame | None = None, children: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    raw: dict[str, Any] = {"type": "Other", "children": list(children)}
    if frame is not None:
        raw["frame"] = {"x": frame.x, "y": frame.y, "width": frame.width, "height": frame.height}
    return raw


def app(frame: Frame, elements: Sequence[Frame]) -> dict[str, Any]:
    return node(frame, [node(element) for element in elements])


def inside(frame: Frame, point: tuple[float, float]) -> bool:
    x, y = point
    return frame.x <= x <= frame.x + frame.width and frame.y <= y <= frame.y + frame.height


def screen(turned: Orientation, showing: Sequence[Frame]) -> Hit:
    """A screen showing elements, in the interface's points, asked as the companion is: at a point of the screen."""

    async def hit(x: float, y: float) -> Frame | None:
        point = Interface(SCREEN, turned).on_interface(x, y)
        return next((frame for frame in showing if inside(frame, point)), None)

    return hit


def asking() -> tuple[Hit, list[tuple[float, float]]]:
    """A screen with nothing anywhere, keeping the points it was asked."""
    asked: list[tuple[float, float]] = []

    async def hit(x: float, y: float) -> Frame | None:
        asked.append((x, y))
        return None

    return hit, asked


async def nothing(x: float, y: float) -> Frame | None:
    return None


async def test_each_orientation_is_found_from_what_is_at_a_point_of_the_screen() -> None:
    # A quarter of the interface at each corner.
    wide = [Frame(x, y, 437, 201) for x in (0, 437) for y in (0, 201)]
    tall = [Frame(x, y, 201, 437) for x in (0, 201) for y in (0, 437)]
    turned: Orientation
    for turned in ("landscape_left", "landscape_right"):
        assert await find(app(LANDSCAPE, wide), SCREEN, screen(turned, wide)) == turned
    for turned in ("portrait", "portrait_upside_down"):
        assert await find(app(UPRIGHT, tall), SCREEN, screen(turned, tall)) == turned


async def test_a_sparse_screen_is_told_by_its_own_elements() -> None:
    # The probe app: four 60-point squares in the interface's corners, and nothing anywhere else.
    squares = [Frame(0, 60, 60, 60), Frame(814, 60, 60, 60), Frame(0, 302, 60, 60), Frame(814, 302, 60, 60)]
    root = node(LANDSCAPE, [node(LANDSCAPE), node(Frame(400, 200, 2, 2)), app(Frame(0, 0, 0, 0), squares)])
    turned: Orientation
    for turned in ("landscape_left", "landscape_right"):
        assert await find(root, SCREEN, screen(turned, squares)) == turned
    # Both ways of the first square are asked at once, and elements nothing is found at tell nothing either.
    hit, asked = asking()
    assert await find(root, SCREEN, hit) == "landscape_right"
    assert asked[:2] == [(312, 30), (90, 844)]
    assert len(asked) == 4 * 2 + 8
    assert len(leaves(root)) == 6


async def test_a_point_that_tells_nothing_is_passed_over() -> None:
    turned_left = screen("landscape_left", [Frame(600, 0, 274, 402)])
    # Nothing, an element with no size, then the whole interface, which both landscapes put every point in.
    first = [None, Frame(0, 0, 0, 0), LANDSCAPE, LANDSCAPE]
    asked: list[tuple[float, float]] = []

    async def hit(x: float, y: float) -> Frame | None:
        asked.append((x, y))
        return first[len(asked) - 1] if len(asked) <= len(first) else await turned_left(x, y)

    assert await find(node(LANDSCAPE), SCREEN, hit) == "landscape_left"
    assert len(asked) == 8


async def test_when_nothing_tells_the_likelier_way_is_taken() -> None:
    assert await find(node(LANDSCAPE), SCREEN, nothing) == "landscape_right"
    assert await find(node(), SCREEN, nothing) == "portrait"
    wide_screen = Screen(2622, 1206, 874, 402, 3)
    assert await find(node(LANDSCAPE), wide_screen, nothing) == "portrait"
    assert candidates(node(Frame(0, 0, 874, 0)), SCREEN) == ("portrait", "portrait_upside_down")


async def test_when_nothing_tells_the_way_the_last_read_found_is_kept_if_it_can_be() -> None:
    assert await find(node(LANDSCAPE), SCREEN, nothing, last="landscape_left") == "landscape_left"
    assert await find(node(UPRIGHT), SCREEN, nothing, last="landscape_left") == "portrait"
    squares = [Frame(0, 60, 60, 60)]
    # What the screen tells comes first.
    told = screen("landscape_right", squares)
    assert await find(app(LANDSCAPE, squares), SCREEN, told, last="landscape_left") == "landscape_right"


async def test_an_element_is_found_by_what_is_inside_it_not_by_a_group_around_everything() -> None:
    """Measured in Reminders: a header group's middle found the heading inside it, to its left; the mirrored point
    found a group the size of the screen."""
    header = Frame(0, 62, 402, 106)
    heading = Frame(16, 120.67, 165, 39.67)

    async def hit(x: float, y: float) -> Frame | None:
        return heading if (x, y) == (201, 115) else UPRIGHT

    assert await find(app(UPRIGHT, [header]), SCREEN, hit) == "portrait"
    # Something small around the middle is that element too.
    label = Frame(150, 50, 100, 130)

    async def around(x: float, y: float) -> Frame | None:
        return label if (x, y) == (201, 115) else None

    assert await find(app(UPRIGHT, [header]), SCREEN, around) == "portrait"


async def test_a_search_that_takes_too_long_takes_the_likelier_way() -> None:
    squares = [Frame(0, 60, 60, 60)]
    left = screen("landscape_left", squares)

    async def slow(x: float, y: float) -> Frame | None:
        await asyncio.sleep(10)
        return await left(x, y)

    assert await find(app(LANDSCAPE, squares), SCREEN, slow, budget_s=0.01) == "landscape_right"


def test_a_frame_is_read_as_the_companion_writes_it() -> None:
    assert frame_of({"x": 1, "y": 2.5, "width": 3, "height": None}) == Frame(1, 2.5, 3, 0)
    assert frame_of(None) is None and frame_of([1, 2]) is None
    assert frame_of({"x": "left"}) is None
    assert leaves({"children": "none", "frame": {"x": 1}}) == [Frame(1, 0, 0, 0)]
    assert leaves({"children": [{"type": "no frame"}, 3]}) == []
