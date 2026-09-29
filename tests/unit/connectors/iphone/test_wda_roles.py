# SPDX-License-Identifier: Apache-2.0
"""What WebDriverAgent does for a session: strokes as W3C actions, keys as text, and its tree as SimMirror's."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any, cast

import pytest

from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.connectors.iphone.wda_client import WdaClient, WdaError
from sim_mirror.connectors.iphone.wda_roles import (
    ORIENTATION_TTL_S,
    SOURCE_TIMEOUT_S,
    STROKE_POINTS,
    Orientation,
    Turn,
    WdaInput,
    WdaReader,
    WdaShots,
    WdaText,
    kept,
    pointer_actions,
    simplified,
)
from sim_mirror.core import gestures
from sim_mirror.testing.fakes import ManualClock
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.wda import FakeWda

SCREEN = Screen(1179, 2556, 393, 852, 3.0)


@pytest.fixture
async def wda() -> AsyncIterator[FakeWda]:
    with short_run_dir() as folder:
        fake = await FakeWda(folder).serve()
        try:
            yield fake
        finally:
            await fake.stop()


async def stream(events: Iterable[HidEvent]) -> AsyncIterator[HidEvent]:
    for event in events:
        yield event


def roles(wda: FakeWda, clock: ManualClock | None = None) -> tuple[WdaInput, WdaReader, Orientation]:
    client = WdaClient(wda.opener())
    clock = clock or ManualClock(10.0)
    orientation = Orientation(client, SCREEN, clock=clock)
    return WdaInput(client, orientation, clock=clock), WdaReader(client, orientation, SCREEN), orientation


def test_a_stroke_is_its_points_with_the_time_between_them() -> None:
    assert pointer_actions([(1.0, 10.0, 20.0), (1.25, 10.0, 120.0), (1.3, 12.5, 200.0)]) == [
        {
            "type": "pointer",
            "id": "finger",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": 10.0, "y": 20.0},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 250, "x": 10.0, "y": 120.0},
                {"type": "pointerMove", "duration": 50, "x": 12.5, "y": 200.0},
                {"type": "pointerUp", "button": 0},
            ],
        }
    ]


def test_a_long_stroke_is_sent_with_few_points_keeping_its_ends_its_last_speed_and_its_bend() -> None:
    straight = [(i * 0.016, 330.0 - i * 10, 500.0) for i in range(25)]
    kept = simplified(straight)
    assert len(kept) == STROKE_POINTS and kept[0] == straight[0] and kept[-2:] == straight[-2:]
    bent = [(i * 0.02, float(i * 10), 500.0 + (40.0 if i == 7 else 0.0)) for i in range(20)]
    assert bent[7] in simplified(bent), "the point farthest off the line keeps the curve"
    short = straight[:3]
    assert simplified(short) is short and simplified(straight, most=3) == [straight[0], *straight[-2:]]


async def test_a_drag_of_many_points_reaches_webdriveragent_as_a_few(wda: FakeWda) -> None:
    clock = ManualClock(10.0)
    sink, _reader, _orientation = roles(wda, clock)

    async def dragged() -> AsyncIterator[HidEvent]:
        yield HidEvent.touch("down", 330, 500)
        for i in range(1, 30):
            clock.now += 0.016
            yield HidEvent.touch("move", 330 - i * 9, 500)
        clock.now += 0.016
        yield HidEvent.touch("up", 60, 500)

    await sink.hid(dragged())
    (body,) = wda.calls("/actions")
    moves = [step for step in body["actions"][0]["actions"] if step["type"] == "pointerMove"]
    assert len(moves) == STROKE_POINTS and (moves[0]["x"], moves[-1]["x"]) == (330.0, 60.0)
    assert sum(step["duration"] for step in moves) == 480, "the stroke keeps its length"


def test_a_sideways_device_turns_points_the_way_its_picture_is_turned_upright() -> None:
    upright, sideways = Turn("PORTRAIT", 393, 852), Turn("LANDSCAPE", 393, 852)
    assert upright.to_interface(10, 20) == (10, 20) == upright.to_portrait(10, 20)
    assert sideways.to_interface(10, 20) == (20, 383) and sideways.to_portrait(20, 383) == (10, 20)
    assert sideways.frame({"x": 20, "y": 353, "width": 100, "height": 30}) == {
        "x": 10.0,
        "y": 20.0,
        "width": 30.0,
        "height": 100.0,
    }
    assert upright.frame({}) == {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}


async def test_a_tap_is_sent_whole_when_the_finger_lifts(wda: FakeWda) -> None:
    clock = ManualClock(10.0)
    sink, _reader, _orientation = roles(wda, clock)

    async def tapped() -> AsyncIterator[HidEvent]:
        yield HidEvent.touch("down", 100, 200)
        clock.now += 0.05
        yield HidEvent.touch("up", 100, 200)

    await sink.hid(tapped())
    (body,) = wda.calls("/actions")
    moves = body["actions"][0]["actions"]
    assert moves[0] == {"type": "pointerMove", "duration": 0, "x": 100.0, "y": 200.0}
    assert moves[2] == {"type": "pointerMove", "duration": 50, "x": 100.0, "y": 200.0}
    await sink.hid(stream([HidEvent.touch("down", 5, 5), HidEvent.touch("down", 6, 9)]))
    assert len(wda.calls("/actions")) == 2, "a finger the stream ends with is lifted where it was"


async def test_points_are_turned_when_the_device_is_on_its_side_and_its_orientation_is_asked_seldom(
    wda: FakeWda,
) -> None:
    clock = ManualClock(10.0)
    sink, _reader, orientation = roles(wda, clock)
    wda.orientation = "LANDSCAPE"
    await sink.hid(stream([HidEvent.touch("down", 10, 20), HidEvent.touch("up", 10, 20)]))
    assert wda.calls("/actions")[0]["actions"][0]["actions"][0] == {
        "type": "pointerMove",
        "duration": 0,
        "x": 20.0,
        "y": 383.0,
    }
    assert len(wda.calls("/orientation")) == 1
    clock.now += ORIENTATION_TTL_S
    assert (await orientation.turn()).sideways and len(wda.calls("/orientation")) == 2


async def test_home_and_lock_are_pressed_and_the_others_are_refused(wda: FakeWda) -> None:
    sink, _reader, _orientation = roles(wda)
    await sink.hid(stream(event for _at, event in gestures.button("home")))
    await sink.hid(stream(event for _at, event in gestures.button("lock")))
    assert [path for _method, path, _body in wda.requests if "wda" in path] == [
        "/wda/homescreen",
        "/session/S1/wda/lock",
    ]
    with pytest.raises(ConnectorError, match="the siri button cannot be pressed through WebDriverAgent"):
        await sink.hid(stream([HidEvent.press("siri", "down")]))


async def test_keys_are_typed_as_the_text_they_make_and_cmd_a_delete_clears_the_field(wda: FakeWda) -> None:
    sink, _reader, _orientation = roles(wda)
    typed = gestures.typed("Hi, World!\n") or []
    await sink.hid(stream(event for _at, event in typed))
    assert wda.calls("/wda/keys") == [{"value": list("Hi, World!\n")}]
    special = [*gestures.key("delete"), *gestures.key("up"), *gestures.key("tab")]
    await sink.hid(stream(event for _at, event in special))
    assert wda.calls("/wda/keys")[-1] == {"value": ["\b", "", "\t"]}
    clearing = [*gestures.typed("ab"), *gestures.select_all(), *gestures.key("delete"), *gestures.typed("c")]
    await sink.hid(stream(event for _at, event in clearing))
    assert wda.calls("/wda/keys")[-2:] == [{"value": ["a", "b"]}, {"value": ["c"]}]
    assert wda.calls("/element/E1/clear") == [None]
    pasted = [event for _at, event in gestures.paste()]
    other = [*pasted, HidEvent.key(999, "down"), HidEvent.touch("down", 1, 1), HidEvent.touch("up", 1, 1)]
    before = len(wda.calls("/wda/keys"))
    await sink.hid(stream(other))
    assert len(wda.calls("/wda/keys")) == before, "Cmd+V and keys that type nothing are dropped"


async def test_clearing_with_no_field_focused_says_so(wda: FakeWda) -> None:
    sink, _reader, _orientation = roles(wda)
    wda.answers[("GET", "/element/active")] = (200, {"value": {}})
    with pytest.raises(ConnectorError, match="no field has the keyboard's focus"):
        await sink.hid(stream(event for _at, event in [*gestures.select_all(), *gestures.key("delete")]))


async def test_text_of_any_kind_is_typed_whole(wda: FakeWda) -> None:
    await WdaText(WdaClient(wda.opener())).type("Привіт, café 👋")
    assert wda.calls("/wda/keys") == [{"value": list("Привіт, café 👋")}]


async def test_the_tree_is_the_document_every_reader_answers(wda: FakeWda) -> None:
    _sink, reader, _orientation = roles(wda)
    document = await reader.accessibility()
    assert document["backend"] == "wda" and document["screen"] == {
        "coordinate_space": "screen",
        "width": 393,
        "height": 852,
    }
    title, field, save = document["elements"]
    assert (title["type"], title["label"], title["value"]) == ("StaticText", "Notes", "Notes")
    assert (field["identifier"], field["value"], field["frame"]["y"]) == ("title", "Groceries", 160.0)
    assert (save["type"], save["enabled"], save["identifier"]) == ("Button", False, "Save")
    wda.answers[("GET", "/source")] = (200, {"value": "not a tree"})
    assert (await reader.accessibility())["elements"] == []


def test_an_element_is_kept_for_what_it_says_or_can_do_and_never_when_invisible() -> None:
    turn = Turn("PORTRAIT", 393, 852)
    plain: dict[str, Any] = {"type": "Other", "children": [{"type": "Switch", "rect": "odd"}, "not a node"]}
    (switch,) = kept(plain, turn)
    assert switch["type"] == "Switch" and switch["label"] is None and switch["enabled"] is True
    assert kept({"type": "Button", "label": "Gone", "isVisible": "0"}, turn) == []


def test_what_shows_under_a_list_webdriveragent_calls_invisible_is_kept() -> None:
    # Settings on iOS 26.3, on an iPhone 14 Pro: its list is invisible, its cells are not.
    row = {"x": 0, "y": 300, "width": 393, "height": 52}
    cell = {"type": "Cell", "label": "General", "isVisible": "1", "rect": row}
    hidden = {"type": "Button", "label": "Off screen", "isVisible": "0"}
    listed = {"type": "CollectionView", "label": "Settings", "isVisible": "0", "children": [cell, hidden]}
    (general,) = kept(listed, Turn("PORTRAIT", 393, 852))
    assert (general["type"], general["label"], general["frame"]["y"]) == ("Cell", "General", 300)


async def test_webdriveragents_screenshot_is_written_as_the_png_it_sends(wda: FakeWda, tmp_path: Path) -> None:
    shots = WdaShots(WdaClient(wda.opener()))
    wda.answers[("GET", "/screenshot")] = (200, {"value": base64.b64encode(b"\x89PNG picture").decode()})
    await shots.screenshot("any", tmp_path / "shot.png")
    assert (tmp_path / "shot.png").read_bytes() == b"\x89PNG picture"
    wda.answers[("GET", "/screenshot")] = (200, {"value": "not base64!"})
    with pytest.raises(ConnectorError, match="WebDriverAgent's screenshot is not a picture"):
        await shots.screenshot("any", tmp_path / "bad.png")


async def test_the_element_tree_is_waited_for_briefly_so_a_screen_it_cannot_read_falls_to_pixels_soon() -> None:
    asked: list[tuple[str, float | None]] = []

    class Slow:
        async def call(self, method: str, path: str, body: Any = None, *, timeout_s: float | None = None) -> Any:
            asked.append((path, timeout_s))
            raise WdaError(f"WebDriverAgent did not answer {method} {path} in time")

        async def in_session(self, method: str, path: str, body: Any = None) -> Any:
            return "PORTRAIT"

    client = cast(WdaClient, Slow())
    reader = WdaReader(client, Orientation(client, SCREEN), SCREEN)
    with pytest.raises(WdaError, match="in time"):
        await reader.accessibility()
    assert asked == [("/source?format=json", SOURCE_TIMEOUT_S)] and SOURCE_TIMEOUT_S <= 5
