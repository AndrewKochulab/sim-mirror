# SPDX-License-Identifier: Apache-2.0
"""What a person does to the screen in a viewer, as input the device takes.

The screen socket accepts the protocol's short list of messages and nothing else (`protocol/v1/client-input`): a finger
going down, moving and lifting; a scroll; a hardware button; a key that is not text; text; the appearance. Coordinates
arrive as shares of the frame -- 0 to 1 across and down -- so the page never needs the device's size, and become points
here.

A finger's down, moves and up are one input stream, as a real finger is one touch: `PersonInput` opens the stream on
down, feeds it on every move, and ends it on up or cancel -- or, for a window that went away mid-drag, lifts the finger
where it last was. Text goes onto the device's pasteboard and is pasted (`gestures.paste`), whatever the Mac's keyboard
layout. Nothing here installs, launches or opens anything: that is the agent tools' business, under their own checks.

On a device whose connector cannot take input (the simctl mirror), only the appearance -- which simctl sets -- is
acted on; everything else is ignored, as a viewer showing a mirror sends none.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from sim_mirror.connectors.base import ConnectorError, HidEvent, InputSink, Screen
from sim_mirror.core import gestures
from sim_mirror.platform.simctl import Simctl
from sim_mirror.protocol import APPEARANCES, KEY_NAMES, PANEL_BUTTONS, SCROLL_MAX_PT, TEXT_MAX_CHARS, TOUCH_PHASES

SCROLL_S = 0.12


@dataclass(frozen=True)
class Command:
    kind: str
    phase: str = ""
    x: float = 0.0
    y: float = 0.0
    dy: float = 0.0
    name: str = ""
    text: str = ""


def _fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _point(message: dict[str, Any], screen: Screen) -> tuple[float, float] | None:
    across, down = _fraction(message.get("nx")), _fraction(message.get("ny"))
    if across is None or down is None:
        return None
    return across * screen.width_pt, down * screen.height_pt


def translate(message: Any, screen: Screen) -> Command | None:
    """The command one viewer message becomes; None for anything not on the list."""
    if not isinstance(message, dict):
        return None
    kind = message.get("type")
    if kind in ("touch", "scroll"):
        point = _point(message, screen)
        if point is None:
            return None
        if kind == "touch":
            phase = message.get("phase")
            return Command("touch", phase=phase, x=point[0], y=point[1]) if phase in TOUCH_PHASES else None
        dy = message.get("dy")
        if isinstance(dy, bool) or not isinstance(dy, (int, float)):
            return None
        return Command("scroll", x=point[0], y=point[1], dy=max(-SCROLL_MAX_PT, min(SCROLL_MAX_PT, float(dy))))
    name = message.get("name")
    if kind == "button":
        return Command("button", name=name) if name in PANEL_BUTTONS else None
    if kind == "key":
        return Command("key", name=name) if name in KEY_NAMES else None
    if kind == "appearance":
        mode = message.get("mode")
        return Command("appearance", name=mode) if mode in APPEARANCES else None
    if kind == "text":
        text = message.get("text")
        if isinstance(text, str) and 0 < len(text) <= TEXT_MAX_CHARS and "\x00" not in text:
            return Command("text", text=text)
    return None


async def _finger(events: asyncio.Queue[HidEvent | None]) -> AsyncIterator[HidEvent]:
    while (event := await events.get()) is not None:
        yield event


class PersonInput:
    """One screen socket's person, acting on one device."""

    def __init__(
        self,
        sink: InputSink | None,
        simctl: Simctl,
        udid: str,
        *,
        on_touch: Callable[[], None] = lambda: None,
    ) -> None:
        self._sink = sink
        self._simctl = simctl
        self._udid = udid
        self._on_touch = on_touch
        self._events: asyncio.Queue[HidEvent | None] | None = None
        self._stream: asyncio.Task[None] | None = None
        self._last = (0.0, 0.0)

    async def run(self, command: Command) -> None:
        if command.kind == "appearance":
            self._on_touch()
            await self._simctl.appearance(self._udid, command.name)
            return
        sink = self._sink
        if sink is None:
            return
        self._on_touch()
        if command.kind == "touch":
            await self._touch(sink, command)
            return
        if command.kind == "scroll":
            # Content scrolls down when the finger moves up.
            events = gestures.swipe((command.x, command.y), (command.x, command.y - command.dy), SCROLL_S)
        elif command.kind == "button":
            events = gestures.button(command.name)
        elif command.kind == "key":
            events = gestures.key(command.name)
        else:
            await self._simctl.pbcopy(self._udid, command.text)
            events = gestures.paste()
        await sink.hid(gestures.play(events))

    async def _touch(self, sink: InputSink, command: Command) -> None:
        if command.phase == "down":
            await self._lift()
            self._events = asyncio.Queue()
            self._stream = asyncio.get_running_loop().create_task(sink.hid(_finger(self._events)))
        elif self._events is None:
            return
        self._last = (command.x, command.y)
        if command.phase in ("down", "move"):
            self._events.put_nowait(HidEvent.touch("down", command.x, command.y))
        else:
            await self._lift()

    async def _lift(self) -> None:
        """End the finger's stream, lifting it where it last was."""
        events, stream = self._events, self._stream
        self._events = self._stream = None
        if events is None or stream is None:
            return
        events.put_nowait(HidEvent.touch("up", *self._last))
        events.put_nowait(None)
        with contextlib.suppress(ConnectorError):
            await stream

    async def close(self) -> None:
        await self._lift()
