# SPDX-License-Identifier: Apache-2.0
"""Gestures as timed input events: what a tap, a swipe or a drag is, touch by touch.

Every function here is pure and returns ``(at_seconds, HidEvent)`` pairs; `play` turns them into the stream an input
sink sends, sleeping until each one is due. The timing is the gesture: iOS reads a swipe's velocity from how far each
move went since the last, so a drag sent all at once is a jump, not a fling.

Text is typed as the keys a US keyboard has for it (`typed`), or pasted (`paste`) -- `core/text_entry.py` chooses.
Keys go through the simulator's keyboard layout, which follows the Mac's input source, so "wifi" typed on a Ukrainian
layout arrives as "цшаш": a key is only the character it looks like on a US-shaped layout.
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
SHIFT_KEY = 225
OPTION_KEY = 226
CONTROL_KEY = 224
#: The modifier keys a step may hold, by the name a step gives them, as HID usage ids.
MODIFIER_KEYS = {"command": COMMAND_KEY, "shift": SHIFT_KEY, "option": OPTION_KEY, "control": CONTROL_KEY}
#: The other names a step may give a modifier: a chord's own, Windows', and the Mac's symbols.
MODIFIER_ALIASES = {
    "cmd": "command", "meta": "command", "⌘": "command", "⇧": "shift",
    "alt": "option", "opt": "option", "⌥": "option", "ctrl": "control", "ctl": "control", "⌃": "control",
}  # fmt: skip
#: Each modifier as a macOS app's key chord names it (``cmd+shift+t``).
CHORD_NAMES = {"command": "cmd", "shift": "shift", "option": "alt", "control": "ctrl"}
#: Other names a chord may give a key that is not text.
KEY_ALIASES = {"enter": "return", "esc": "escape", "backspace": "delete"}
#: The most taps one step plays: a triple click selects a paragraph.
TAPS_MAX = 3
#: How long a finger is off the glass between the taps of a double tap: well inside the ~0.3 s iOS and Android allow.
TAP_GAP_S = 0.1
A_KEY = 4
V_KEY = 25
#: How long each typed key is held, with the next pressed as it lifts. Measured with 164 characters (#27): held for
#: no time at all, iOS 27.0 lost the last 40 of them; held 10ms it kept every one, on 26.5 too. Twice that is kept.
KEY_HOLD_S = 0.02

_LETTERS = {letter: 4 + index for index, letter in enumerate("abcdefghijklmnopqrstuvwxyz")}
#: The HID usage id of every character a US keyboard types, and whether Shift is held for it.
CHARACTER_KEYS: dict[str, tuple[int, bool]] = {
    **{letter: (code, False) for letter, code in _LETTERS.items()},
    **{letter.upper(): (code, True) for letter, code in _LETTERS.items()},
    **dict(zip("1234567890", ((code, False) for code in range(30, 40)), strict=True)),
    **dict(zip("!@#$%^&*()", ((code, True) for code in range(30, 40)), strict=True)),
    "\n": (40, False), " ": (44, False),
    "-": (45, False), "=": (46, False), "[": (47, False), "]": (48, False), "\\": (49, False),
    ";": (51, False), "'": (52, False), "`": (53, False), ",": (54, False), ".": (55, False), "/": (56, False),
    "_": (45, True), "+": (46, True), "{": (47, True), "}": (48, True), "|": (49, True),
    ":": (51, True), '"': (52, True), "~": (53, True), "<": (54, True), ">": (55, True), "?": (56, True),
}  # fmt: skip

Point = tuple[float, float]
Timed = tuple[float, HidEvent]


def tap(x: float, y: float, hold_s: float = TAP_S) -> list[Timed]:
    return [(0.0, HidEvent.touch("down", x, y)), (hold_s, HidEvent.touch("up", x, y))]


def taps(x: float, y: float, count: int, hold_s: float = TAP_S, gap_s: float = TAP_GAP_S) -> list[Timed]:
    """`count` taps on one point, close enough together to be one double (or triple) tap."""
    events: list[Timed] = []
    for index in range(count):
        at = index * (hold_s + gap_s)
        events += [(at + when, event) for when, event in tap(x, y, hold_s)]
    return events


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


def modifiers(value: object) -> tuple[str, ...]:
    """The modifier keys a step holds, by their names (`MODIFIER_KEYS`), in that order. Raises ValueError for anything
    else: a step that asked for one it cannot hold is refused, never played without it."""
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names or len(names) > len(MODIFIER_KEYS):
        raise ValueError('modifiers is a list of command, shift, option and control, e.g. ["command", "shift"]')
    held: set[str] = set()
    for name in names:
        canonical = MODIFIER_ALIASES.get(name.lower(), name.lower()) if isinstance(name, str) else None
        if canonical not in MODIFIER_KEYS:
            raise ValueError(f"not a modifier: {str(name)[:40]!r}; modifiers are command, shift, option and control")
        held.add(canonical)
    return tuple(name for name in MODIFIER_KEYS if name in held)


def key_code(name: str) -> tuple[int, bool] | None:
    """The HID usage id of a key a step names -- a key that is not text (`KEYS`) or one character a US keyboard types
    -- and whether Shift is held for it; None when there is no such key."""
    named = KEY_ALIASES.get(name.lower(), name.lower())
    if named in KEYS:
        return KEYS[named], False
    return CHARACTER_KEYS.get(name) if len(name) == 1 else None


def chord(held: Sequence[str], code: int, hold_s: float = TAP_S) -> list[Timed]:
    """A key struck while `held` modifiers are down: each goes down first and comes up after it."""
    keys = [MODIFIER_KEYS[name] for name in held]
    return [
        *((0.0, HidEvent.key(modifier, "down")) for modifier in keys),
        (0.0, HidEvent.key(code, "down")),
        (hold_s, HidEvent.key(code, "up")),
        *((hold_s, HidEvent.key(modifier, "up")) for modifier in reversed(keys)),
    ]


def chords(text: str) -> list[tuple[tuple[str, ...], int]]:
    """Key chords as a step writes them -- ``cmd+shift+t``, several separated by spaces -- as the modifiers each holds
    and the key it strikes. Raises ValueError naming what is not a modifier or a key."""
    parsed: list[tuple[tuple[str, ...], int]] = []
    for written in text.split():
        # "cmd++" strikes "+" itself, as a lone "+" does.
        if written == "+":
            names, key = [], "+"
        elif written.endswith("++"):
            names, key = written[:-2].split("+"), "+"
        else:
            *names, key = written.split("+")
        if not key or any(not name for name in names):
            raise ValueError(f"a chord is modifiers and a key joined by +, such as cmd+shift+t, not {written!r}")
        held = modifiers(names) if names else ()
        found = key_code(key)
        if found is None:
            raise ValueError(
                f"no key {key!r} to press here; a key is a letter, a digit, punctuation or {', '.join(KEYS)}"
            )
        code, shifted = found
        parsed.append((modifiers([*held, "shift"]) if shifted else held, code))
    if not parsed:
        raise ValueError("keys names one or more chords, e.g. cmd+shift+t")
    return parsed


def typed(text: str, hold_s: float = KEY_HOLD_S) -> list[Timed] | None:
    """Text as the key presses a US keyboard makes it with, one after another; None when a character has no key --
    an accented letter, an emoji, a tab -- so the text is pasted whole rather than typed in part."""
    events: list[Timed] = []
    at = 0.0
    for character in text:
        key_of = CHARACTER_KEYS.get(character)
        if key_of is None:
            return None
        code, shifted = key_of
        if shifted:
            events.append((at, HidEvent.key(SHIFT_KEY, "down")))
        events += [(at, HidEvent.key(code, "down")), (at + hold_s, HidEvent.key(code, "up"))]
        if shifted:
            events.append((at + hold_s, HidEvent.key(SHIFT_KEY, "up")))
        at += hold_s
    return events


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
