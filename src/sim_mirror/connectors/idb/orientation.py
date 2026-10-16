# SPDX-License-Identifier: Apache-2.0
"""Which way the interface idb_companion reads is turned on the screen. idb_companion does not say, so it is found.

Measured with idb_companion on an iPhone 17 Pro simulator (iOS 26.5), an app turning its own interface to each
landscape: the tree is in the interface's points -- its root, the frontmost application, 874 by 402 on a screen 402 by
874 -- while ``describe``, screenshots and touches stay the screen's portrait points. Asked what is at a point
(``accessibility_info`` with a ``point``), it takes the screen's portrait points and answers the element there in the
interface's: in an app turned landscape left, the screen's (90, 30) is the button at the interface's top right, at
(814, 60). A point with nothing there, or off the screen, is an error.

That is how the native helper's accessibility behaves, so the orientation is found the way the helper finds it
(`find`): sideways or upright from the root's shape, and which of the two from what is at a few points of the screen.
Each point asked is a round trip to the companion, measured at 4 to 17 ms, so the two ways are asked together and the
whole search has a budget (`BUDGET_S`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import Frame
from sim_mirror.perception.orientation import Interface, Orientation

#: What is at a point of the screen, in its portrait points: the frame of the element there in the interface's
#: points, or None when nothing is or the companion would not say.
Hit = Callable[[float, float], Awaitable[Frame | None]]

#: How many of a tree's elements are looked for on the screen before points of it are.
ELEMENTS_ASKED = 4
#: The share of the screen probed, across and down: inside the edges, and nowhere two orientations put the same
#: interface point. The helper's.
PROBES = ((0.3, 0.25), (0.7, 0.25), (0.3, 0.75), (0.7, 0.75), (0.5, 0.15), (0.5, 0.85), (0.2, 0.5), (0.8, 0.5))
#: How long finding the orientation may take before the likelier of the two ways is taken.
BUDGET_S = 0.5
#: How far outside a frame a point may be and still be in it: frames are fractions of a point.
SLACK = 0.5


def frame_of(raw: object) -> Frame | None:
    """A frame as the companion writes one, or None."""
    if not isinstance(raw, Mapping):
        return None
    try:
        return Frame(*(float(raw.get(key) or 0) for key in ("x", "y", "width", "height")))
    except (TypeError, ValueError):
        return None


def _contains(frame: Frame, point: tuple[float, float]) -> bool:
    x, y = point
    return (
        frame.x - SLACK <= x <= frame.x + frame.width + SLACK and frame.y - SLACK <= y <= frame.y + frame.height + SLACK
    )


def _within(inner: Frame, outer: Frame) -> bool:
    return _contains(outer, (inner.x, inner.y)) and _contains(outer, (inner.x + inner.width, inner.y + inner.height))


def _found(there: Frame | None, element: Frame, most: float) -> bool:
    """Whether what is at an element's middle is that element: it, something inside it, or something small around it.

    Measured in Reminders: the tree's interactable elements are groups, and their middles find the text inside them --
    the heading in a header, not over its middle -- while the mirrored point found a group as big as the screen, which
    holds every middle and so tells nothing.
    """
    if there is None:
        return False
    middle = (element.x + element.width / 2, element.y + element.height / 2)
    return _within(there, element) or (_contains(there, middle) and there.width * there.height <= most)


def leaves(node: Mapping[str, Any]) -> list[Frame]:
    """The frames of the elements with nothing inside them, in the tree's order."""
    children = node.get("children")
    kids = [child for child in children if isinstance(child, Mapping)] if isinstance(children, list) else []
    if kids:
        return [frame for kid in kids for frame in leaves(kid)]
    frame = frame_of(node.get("frame"))
    return [] if frame is None else [frame]


def candidates(root: Mapping[str, Any], screen: Screen) -> tuple[Orientation, Orientation]:
    """The two ways an interface of the root's shape can lie on the screen, the likelier first.

    An interface as wide as the screen is portrait or upside down; one as wide as the screen is tall is one of the
    landscapes, landscape right first -- where UIKit puts an app that takes either.
    """
    interface = frame_of(root.get("frame"))
    wide_screen = screen.width_pt > screen.height_pt
    quarter = (
        interface is not None
        and interface.width > 0
        and interface.height > 0
        and (interface.width > interface.height) != wide_screen
    )
    return ("landscape_right", "landscape_left") if quarter else ("portrait", "portrait_upside_down")


async def find(
    root: Mapping[str, Any],
    screen: Screen,
    hit: Hit,
    *,
    last: Orientation | None = None,
    budget_s: float = BUDGET_S,
) -> Orientation:
    """Which way the root's interface lies on the screen.

    When nothing tells within `budget_s`, the way the `last` read found, if it is one of the two; else the likelier.
    Measured: a permission alert over Maps turned landscape left answered no point at all -- its frames are its
    landscape ones with across and down swapped -- and taking landscape right would have turned everything around.
    """
    ways = candidates(root, screen)
    try:
        told = await asyncio.wait_for(_told(root, screen, hit, ways), budget_s)
    except asyncio.TimeoutError:
        told = None
    return told or (last if last in ways else ways[0])


async def _told(
    root: Mapping[str, Any], screen: Screen, hit: Hit, ways: tuple[Orientation, Orientation]
) -> Orientation | None:
    """The way the screen tells, asked as the helper asks; None when nothing tells.

    The middle of one of the tree's small elements is asked for where each way would put it on the screen: only the
    right one finds that element there (`_found`). Then points spread over the screen are asked: only the right one
    puts the point inside what is found. A point that finds nothing, or an element both put it in, tells nothing.
    """
    interfaces = [Interface(screen, way) for way in ways]
    most = screen.width_pt * screen.height_pt / 4
    small = [leaf for leaf in leaves(root) if min(leaf.width, leaf.height) >= 4 and leaf.width * leaf.height <= most]
    for frame in small[:ELEMENTS_ASKED]:
        middle = (frame.x + frame.width / 2, frame.y + frame.height / 2)
        answers = await asyncio.gather(*(hit(*interface.on_device(*middle)) for interface in interfaces))
        told = _one(ways, [_found(there, frame, most) for there in answers])
        if told:
            return told
    points = [(across * screen.width_pt, down * screen.height_pt) for across, down in PROBES]
    for point, there in zip(points, await asyncio.gather(*(hit(*point) for point in points)), strict=True):
        if there is None or there.width <= 0 or there.height <= 0:
            continue
        told = _one(ways, [_contains(there, interface.on_interface(*point)) for interface in interfaces])
        if told:
            return told
    return None


def _one(ways: Sequence[Orientation], fits: Sequence[bool]) -> Orientation | None:
    """The one way that fits; None when both or neither do."""
    fitting = [way for way, fit in zip(ways, fits, strict=True) if fit]
    return fitting[0] if len(fitting) == 1 else None
