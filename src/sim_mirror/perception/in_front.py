# SPDX-License-Identifier: Apache-2.0
"""Whether a tap on an element lands on it, asked of the device as the tap is about to be played.

A snapshot says what is in front from the tree a reader listed (`perception.occlusion`), and two things that tree does
not say are asked of the device instead, through its accessibility's hit test -- what is at a point of the screen
(`connectors.base.PointReader`):

- **What is drawn in front of a bar.** Accessibility lists elements in the order a person reads them, not the order
  they are drawn, so a button floating over a tab bar reads as under it: ``covered by tab bar``. Asked at its point,
  the hit test answers the button itself, and the tap is played; under the bar it answers a tab or the bar.
- **What is under a navigation bar no reader lists.** Settings and Health list none, so a row scrolled up under the bar
  reads as clear. An element whose point is near the top of the screen, where a navigation bar would be, is asked at
  its point: when something else answers -- the bar's title, a status bar item -- the tap is refused as one under a
  bar is. When part of it is below that band, the middle of that part is asked too, and the tap goes there when it
  answers the element.

Measured on an iPhone 17 Pro simulator (iOS 26.5, 2026-10-16), through idb_companion: a button floating over the tab
bar answered itself and a row under the bar answered the tab over it; in Settings scrolled up, points from y 62 to 115
answered the navigation bar's title "Settings", points above y 51 the status bar's items, and a point at y 55 the
whole screen; in Health the profile button and the large title answered themselves. Each point took 4 to 30 ms, the
points of one tap asked at once.

What answers is weighed against the element's frame: the element itself -- the same frame, give or take `ITSELF_PT` --
or something else. Something around the element, holding all of it, says nothing either way -- an element some hit
tests pass over for what holds it -- and neither does a point that answers nothing: the tap is then played, or
refused, as the snapshot said. So it is when the device cannot be asked or does not answer within `BUDGET_S`.

The hit test is not the touch: in Files, a point at y 59 -- inside the status bar's strip, below the Dynamic Island --
answered the row there, and a tap at that point opened nothing. So an element covered by the status bar or the
keyboard is not asked about, and a point below the band is asked first.

This is asked when a touch is about to begin on a ref, not when a snapshot is read: a snapshot is read after every
step and on every poll of a wait, while a tap on a ref is played once -- and the answer is then about the screen as
it is when the touch lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import ElementNode, Frame
from sim_mirror.perception.occlusion import (
    CLEAR_MIN_PT,
    EDGE_PT,
    KEYBOARD,
    OCCLUDER_KINDS,
    STATUS_BAR,
    STATUS_BAR_TRAIT,
)
from sim_mirror.perception.snapshot import Element

#: The band at the top of the screen a navigation bar takes, in points: the status bar's strip and the bar, 62 and 54
#: on an iPhone 17 Pro (measured). On a phone with less above its bar the band reaches into what is below it, which is
#: only asked about more.
NAVIGATION_BAND_PT = 116.0
#: How far each edge of what answers may be from the element's for it to be the element itself, in points.
ITSELF_PT = 1.0
#: How long asking may take before a tap is played as the snapshot said.
BUDGET_S = 0.5
NAVIGATION_BAR = OCCLUDER_KINDS["NavigationBar"]

Point = tuple[float, float]


def asked(element: Element, covers: frozenset[str], screen: Screen) -> tuple[Point, ...]:
    """The points of the interface to ask what is at before a touch on this element: its own point first. None when
    its line is to be trusted: no frame to weigh an answer against, no cover the device can say more of, and no band
    left unlisted. `covers` is what the snapshot's tree said covers the screen, and `screen` the interface's."""
    frame = element.frame
    if frame is None:
        return ()
    if element.covered is not None:
        return () if element.covered in (KEYBOARD, STATUS_BAR) else ((element.x, element.y),)
    if NAVIGATION_BAR in covers or element.y >= NAVIGATION_BAND_PT:
        return ()
    bottom = min(frame.y + frame.height, float(screen.height_pt))
    if bottom - NAVIGATION_BAND_PT < CLEAR_MIN_PT:
        return ((element.x, element.y),)
    return (element.x, element.y), (element.x, (NAVIGATION_BAND_PT + bottom) / 2)


def _itself(answer: Frame, frame: Frame) -> bool:
    return (
        abs(answer.x - frame.x) <= ITSELF_PT
        and abs(answer.y - frame.y) <= ITSELF_PT
        and abs(answer.x + answer.width - (frame.x + frame.width)) <= ITSELF_PT
        and abs(answer.y + answer.height - (frame.y + frame.height)) <= ITSELF_PT
    )


def _around(answer: Frame, frame: Frame) -> bool:
    """Whether what answered holds all of the element, give or take `EDGE_PT`."""
    return (
        answer.x <= frame.x + EDGE_PT
        and answer.y <= frame.y + EDGE_PT
        and answer.x + answer.width >= frame.x + frame.width - EDGE_PT
        and answer.y + answer.height >= frame.y + frame.height - EDGE_PT
    )


def judged(element: Element, points: Sequence[Point], answers: Sequence[ElementNode | None]) -> Element:
    """The element as what is at `points` says a touch on it lands: in front of what covered it, moved to the point
    below the band that found it, or covered by what is in front of it. As it was when nothing tells."""
    frame = element.frame
    assert frame is not None, "only an element with a frame is asked about"
    found = [answer is not None and answer.frame is not None and _itself(answer.frame, frame) for answer in answers]
    if element.covered is not None:
        return replace(element, covered=None) if found[0] else element
    if len(points) > 1 and found[1]:
        return replace(element, x=points[1][0], y=points[1][1])
    there = answers[0]
    if found[0] or there is None or there.frame is None or _around(there.frame, frame):
        return element
    return replace(element, covered=STATUS_BAR if STATUS_BAR_TRAIT in there.traits else NAVIGATION_BAR)
