# SPDX-License-Identifier: Apache-2.0
"""Gestures as timed input events: what a tap, a swipe or a drag is, touch by touch.

Every function here is pure and returns ``(at_seconds, HidEvent)`` pairs; `play` turns them into the stream an input
sink sends, sleeping until each one is due. The timing is the gesture: iOS reads a swipe's velocity from how far each
move went since the last, so a drag sent all at once is a jump, not a fling.

Text is not typed here. Keys go through the simulator's keyboard layout, which follows the Mac's input source, so
"wifi" typed on a Ukrainian layout arrives as "цшаш"; text goes onto the device's pasteboard and `paste` presses Cmd+V.
Only keys that are not text are pressed.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from itertools import pairwise

from sim_mirror.connectors.base import HidEvent

#: One move per displayed frame.
STEP_S = 1 / 60
#: How long a finger rests for a tap, a button for a press, a key for a stroke.
TAP_S = 0.05
LONG_PRESS_S = 0.8
SWIPE_S = 0.3

#: HID usage ids of the keys that are not text, in the protocol's order of `KEY_NAMES`.
KEYS = {
    "return": 40, "escape": 41, "delete": 42, "tab": 43, "space": 44,
    "right": 79, "left": 80, "down": 81, "up": 82,
}  # fmt: skip
COMMAND_KEY = 227
A_KEY = 4
V_KEY = 25

Point = tuple[float, float]
Timed = tuple[float, HidEvent]


def tap(x: float, y: float, hold_s: float = TAP_S) -> list[Timed]:
    return [(0.0, HidEvent.touch("down", x, y)), (hold_s, HidEvent.touch("up", x, y))]


def long_press(x: float, y: float, hold_s: float = LONG_PRESS_S) -> list[Timed]:
    return tap(x, y, hold_s)


def _along(points: Sequence[Point], fraction: float) -> Point:
    """The point `fraction` of the way along a polyline, by distance."""
    segments = list(pairwise(points))
    lengths = [math.dist(a, b) for a, b in segments]
    remaining = fraction * sum(lengths)
    for (a, b), length in zip(segments, lengths, strict=True):
        if remaining <= length and length > 0:
            share = remaining / length
            return a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share
        remaining -= length
    return points[-1]


def drag(points: Sequence[Point], duration_s: float) -> list[Timed]:
    """A finger down on the first point, moving along the rest at an even speed, up on the last."""
    if not points:
        raise ValueError("a drag needs at least one point")
    duration_s = max(duration_s, STEP_S)
    steps = max(1, round(duration_s / STEP_S))
    events: list[Timed] = [(0.0, HidEvent.touch("down", *points[0]))]
    for index in range(1, steps):
        events.append((duration_s * index / steps, HidEvent.touch("down", *_along(points, index / steps))))
    events.append((duration_s, HidEvent.touch("up", *points[-1])))
    return events


def swipe(start: Point, end: Point, duration_s: float = SWIPE_S) -> list[Timed]:
    return drag([start, end], duration_s)


def key(name: str) -> list[Timed]:
    if name not in KEYS:
        raise ValueError(f"not a key: {name!r}")
    code = KEYS[name]
    return [(0.0, HidEvent.key(code, "down")), (TAP_S, HidEvent.key(code, "up"))]


def button(name: str) -> list[Timed]:
    return [(0.0, HidEvent.press(name, "down")), (TAP_S, HidEvent.press(name, "up"))]


def _command(code: int) -> list[Timed]:
    return [
        (0.0, HidEvent.key(COMMAND_KEY, "down")),
        (0.0, HidEvent.key(code, "down")),
        (TAP_S, HidEvent.key(code, "up")),
        (TAP_S, HidEvent.key(COMMAND_KEY, "up")),
    ]


def paste() -> list[Timed]:
    """Cmd+V: what puts the pasteboard's text into the focused field."""
    return _command(V_KEY)


def select_all() -> list[Timed]:
    """Cmd+A: everything the focused field holds, so the next keystroke replaces it."""
    return _command(A_KEY)


def duration(events: Sequence[Timed]) -> float:
    return max((at for at, _event in events), default=0.0)


async def play(
    events: Sequence[Timed],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[HidEvent]:
    """The events, each yielded when it is due."""
    start = clock()
    for at, event in events:
        wait = start + at - clock()
        if wait > 0:
            await sleep(wait)
        yield event
