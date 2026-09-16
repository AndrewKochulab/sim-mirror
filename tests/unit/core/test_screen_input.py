# SPDX-License-Identifier: Apache-2.0
"""A person's input from a viewer: only the whitelist, in points, one stream per finger, text by the pasteboard, and on
a mirror only the appearance."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError, HidEvent
from sim_mirror.core import gestures
from sim_mirror.core.screen_input import Command, PersonInput, translate
from sim_mirror.platform.simctl import Simctl
from sim_mirror.protocol import SCROLL_MAX_PT, TEXT_MAX_CHARS
from sim_mirror.testing.fakes import BOOTED_UDID, SCREEN, FakeEngine, FakeKeyboard, FakeXcrun


@pytest.mark.parametrize(
    ("message", "command"),
    [
        ({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.25}, Command("touch", phase="down", x=201.0, y=218.5)),
        ({"type": "touch", "phase": "move", "nx": 0, "ny": 1}, Command("touch", phase="move", x=0.0, y=874.0)),
        ({"type": "touch", "phase": "cancel", "nx": 1, "ny": 0}, Command("touch", phase="cancel", x=402.0, y=0.0)),
        ({"type": "touch", "phase": "hover", "nx": 0.5, "ny": 0.5}, None),
        ({"type": "touch", "phase": "down", "nx": 1.5, "ny": 0.5}, None),
        ({"type": "touch", "phase": "down", "nx": True, "ny": 0.5}, None),
        ({"type": "touch", "phase": "down", "nx": "0.5", "ny": 0.5}, None),
        ({"type": "scroll", "nx": 0.5, "ny": 0.5, "dy": 120}, Command("scroll", x=201.0, y=437.0, dy=120.0)),
        (
            {"type": "scroll", "nx": 0.5, "ny": 0.5, "dy": -99999},
            Command("scroll", x=201.0, y=437.0, dy=-SCROLL_MAX_PT),
        ),
        ({"type": "scroll", "nx": 0.5, "ny": 0.5, "dy": False}, None),
        ({"type": "scroll", "nx": 0.5, "ny": 0.5}, None),
        ({"type": "scroll", "nx": -1, "ny": 0.5, "dy": 10}, None),
        ({"type": "button", "name": "home"}, Command("button", name="home")),
        ({"type": "button", "name": "apple_pay"}, None),
        ({"type": "key", "name": "return"}, Command("key", name="return")),
        ({"type": "key", "name": "f13"}, None),
        ({"type": "appearance", "mode": "dark"}, Command("appearance", name="dark")),
        ({"type": "appearance", "mode": "sepia"}, None),
        ({"type": "text", "text": "café 😀"}, Command("text", text="café 😀")),
        ({"type": "text", "text": ""}, None),
        ({"type": "text", "text": "x" * (TEXT_MAX_CHARS + 1)}, None),
        ({"type": "text", "text": "a\x00b"}, None),
        ({"type": "text", "text": 42}, None),
        ({"type": "install", "path": "/tmp/Evil.app"}, None),
        ({"type": "open_url", "url": "file:///etc/hosts"}, None),
        (["touch"], None),
    ],
)
def test_translate_is_a_whitelist_and_speaks_in_points(message: Any, command: Command | None) -> None:
    assert translate(message, SCREEN) == command


def make(
    engine: FakeEngine | None = None, *, mirror: bool = False, typing: str = "auto", us: bool = False
) -> tuple[PersonInput, FakeEngine, FakeXcrun, list[int]]:
    engine = engine or FakeEngine()
    xcrun = FakeXcrun()
    touched: list[int] = []
    person = PersonInput(
        None if mirror else engine,
        Simctl(xcrun),
        BOOTED_UDID,
        on_touch=lambda: touched.append(1),
        typing=typing,
        keyboard_is_us=FakeKeyboard(us=us),
    )
    return person, engine, xcrun, touched


async def test_a_finger_is_one_stream_from_down_through_its_moves_to_up() -> None:
    person, engine, _xcrun, touched = make()
    await person.run(Command("touch", phase="down", x=10, y=20))
    await person.run(Command("touch", phase="move", x=10, y=40))
    await person.run(Command("touch", phase="up", x=10, y=60))
    assert engine.hid_events == [
        HidEvent.touch("down", 10, 20),
        HidEvent.touch("down", 10, 40),
        HidEvent.touch("up", 10, 60),
    ]
    assert len(touched) == 3


async def test_a_stray_move_is_ignored_and_a_finger_left_down_is_lifted_where_it_was() -> None:
    person, engine, _xcrun, _touched = make()
    await person.run(Command("touch", phase="move", x=1, y=1))
    await person.run(Command("touch", phase="up", x=1, y=1))
    await person.run(Command("touch", phase="down", x=5, y=5))
    await person.run(Command("touch", phase="down", x=9, y=9))
    await person.run(Command("touch", phase="move", x=9, y=12))
    await person.close()
    await person.close()
    assert engine.hid_events == [
        HidEvent.touch("down", 5, 5),
        HidEvent.touch("up", 5, 5),
        HidEvent.touch("down", 9, 9),
        HidEvent.touch("down", 9, 12),
        HidEvent.touch("up", 9, 12),
    ]
    await person.run(Command("touch", phase="down", x=3, y=3))
    await person.run(Command("touch", phase="cancel", x=4, y=4))
    assert engine.hid_events[-1] == HidEvent.touch("up", 4, 4)


async def test_a_finger_whose_stream_broke_is_let_go_quietly() -> None:
    class Broken(FakeEngine):
        async def hid(self, events: AsyncIterable[HidEvent]) -> None:
            async for _event in events:
                raise ConnectorError("sending input failed: companion gone")

    person, _engine, _xcrun, _touched = make(Broken())
    await person.run(Command("touch", phase="down", x=1, y=1))
    for _ in range(10):
        await asyncio.sleep(0)
    await person.run(Command("touch", phase="up", x=1, y=1))


async def test_scrolls_buttons_keys_and_text_become_gestures_and_appearance_a_setting() -> None:
    person, engine, xcrun, _touched = make()
    await person.run(Command("scroll", x=200, y=400, dy=100))
    assert engine.hid_events[0] == HidEvent.touch("down", 200, 400)
    assert engine.hid_events[-1] == HidEvent.touch("up", 200, 300)
    engine.hid_events.clear()
    await person.run(Command("button", name="home"))
    assert engine.hid_events == [event for _at, event in gestures.button("home")]
    engine.hid_events.clear()
    await person.run(Command("key", name="return"))
    assert [event.code for event in engine.hid_events] == [40, 40]
    engine.hid_events.clear()
    await person.run(Command("text", text="café"))
    assert xcrun.calls[-1].args == ("simctl", "pbcopy", BOOTED_UDID) and xcrun.calls[-1].input_data == "café".encode()
    assert [event.code for event in engine.hid_events] == [
        gestures.COMMAND_KEY,
        gestures.V_KEY,
        gestures.V_KEY,
        gestures.COMMAND_KEY,
    ]
    await person.run(Command("appearance", name="dark"))
    assert xcrun.calls[-1].args == ("simctl", "ui", BOOTED_UDID, "appearance", "dark")


async def test_text_is_typed_as_keys_where_it_can_be_and_the_setting_allows_and_pasted_otherwise() -> None:
    person, engine, xcrun, _touched = make(us=True)
    await person.run(Command("text", text="Hi"))
    assert xcrun.calls == [] and engine.hid_events == [event for _at, event in gestures.typed("Hi") or []]
    for typing, us in (("auto", False), ("paste", True)):
        person, engine, xcrun, _touched = make(typing=typing, us=us)
        await person.run(Command("text", text="Hi"))
        assert xcrun.argv() == [("simctl", "pbcopy", BOOTED_UDID)] and len(engine.hid_events) == 4
    person, engine, xcrun, _touched = make(typing="keys", us=False)
    await person.run(Command("text", text="Hi"))
    assert xcrun.calls == [] and len(engine.hid_events) == 6


async def test_a_mirror_takes_only_the_appearance() -> None:
    person, engine, xcrun, touched = make(mirror=True)
    for command in (Command("touch", phase="down", x=1, y=1), Command("key", name="return"), Command("text", text="x")):
        await person.run(command)
    assert engine.hid_events == [] and xcrun.calls == [] and touched == []
    await person.run(Command("appearance", name="light"))
    assert xcrun.argv() == [("simctl", "ui", BOOTED_UDID, "appearance", "light")] and touched == [1]
    await person.close()
