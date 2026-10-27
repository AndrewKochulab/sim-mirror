# SPDX-License-Identifier: Apache-2.0
"""What WebDriverAgent does for a session: strokes as W3C actions, keys as text, and its tree as SimMirror's."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.connectors.iphone.wda_client import SOURCE_MAX_CHILDREN, WdaClient, WdaError
from sim_mirror.connectors.iphone.wda_roles import (
    COLD_SOURCE_TIMEOUT_S,
    EXCLUDED,
    INPUT_WAIT_S,
    NOT_SENT,
    ORIENTATION_TTL_S,
    SOURCE_TIMEOUT_S,
    STROKE_POINTS,
    Orientation,
    Turn,
    WdaInput,
    WdaReader,
    WdaShots,
    WdaText,
    cut_short,
    kept,
    modal_of,
    pointer_actions,
    simplified,
)
from sim_mirror.core import gestures
from sim_mirror.perception.model import Modal
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import build
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


def test_a_bar_or_the_keyboard_is_kept_though_it_says_nothing_since_it_covers_what_is_under_it() -> None:
    turn = Turn("PORTRAIT", 393, 852)
    key = {"type": "Key", "label": "Q", "rect": {"x": 0, "y": 600, "width": 40, "height": 50}}
    keyboard = {"type": "Keyboard", "rect": {"x": 0, "y": 590, "width": 393, "height": 262}, "children": [key]}
    bar = {"type": "TabBar", "rect": {"x": 0, "y": 769, "width": 393, "height": 83}}
    found = [kept(each, turn)[0] for each in (keyboard, bar)]
    assert [(each["type"], len(each["children"])) for each in found] == [("Keyboard", 1), ("TabBar", 0)]


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


class CableShots:
    """devicectl as `WdaShots` falls back to: a screenshot written where asked."""

    def __init__(self) -> None:
        self.taken: list[str] = []

    async def screenshot(self, udid: str, destination: Path) -> None:
        self.taken.append(udid)
        destination.write_bytes(b"\x89PNG from devicectl")


async def test_a_screenshot_does_not_wait_behind_a_tree_webdriveragent_is_still_building(
    wda: FakeWda, tmp_path: Path
) -> None:
    devicectl = CableShots()
    client = WdaClient(wda.opener(), probe_timeout_s=0.05)
    shots = WdaShots(client, fallback=devicectl)
    wda.answers[("GET", "/screenshot")] = (200, {"value": base64.b64encode(b"\x89PNG from WebDriverAgent").decode()})
    await shots.screenshot("phone", tmp_path / "free.png")
    assert (tmp_path / "free.png").read_bytes() == b"\x89PNG from WebDriverAgent" and devicectl.taken == []
    wda.source_delay_s = 0.4
    with pytest.raises(WdaError):
        await client.call("GET", "/source?format=json", timeout_s=0.05)
    await shots.screenshot("phone", tmp_path / "busy.png")
    assert (tmp_path / "busy.png").read_bytes() == b"\x89PNG from devicectl" and devicectl.taken == ["phone"]
    assert [path for _method, path, _body in wda.requests].count("/screenshot") == 1
    # With nothing to fall back on, WebDriverAgent takes it, as before.
    await asyncio.sleep(0.4)
    await WdaShots(client).screenshot("phone", tmp_path / "after.png")
    assert (tmp_path / "after.png").read_bytes() == b"\x89PNG from WebDriverAgent"


async def test_the_tree_is_asked_for_only_what_is_read_within_bounds_under_the_session(wda: FakeWda) -> None:
    client = WdaClient(wda.opener())
    reader = WdaReader(client, Orientation(client, SCREEN), SCREEN, max_children=60)
    await reader.accessibility()
    (asked,) = [path for method, path, _body in wda.requests if "/source" in path]
    assert asked == f"/session/S1/source?format=json&excluded_attributes={','.join(EXCLUDED)}"
    assert "visible" not in EXCLUDED and "enabled" not in EXCLUDED
    assert wda.calls("/appium/settings")[0]["settings"]["snapshotMaxChildren"] == 60
    await reader.accessibility()
    assert len(wda.calls("/appium/settings")) == 1, "given once, with the session"


async def test_a_tree_not_built_in_time_says_so_and_the_next_read_does_not_ask_again_while_it_is_built(
    wda: FakeWda,
) -> None:
    client = WdaClient(wda.opener(), probe_timeout_s=0.05)
    reader = WdaReader(client, Orientation(client, SCREEN), SCREEN, timeout_s=0.1, cold_timeout_s=0.1)
    wda.source_delay_s = 0.4
    with pytest.raises(WdaError, match=r"did not build the element tree within 0\.1 s \(real_devices\.wda\.source"):
        await reader.accessibility()
    with pytest.raises(WdaError, match="still being built on the device for an earlier read"):
        await reader.accessibility()
    assert len([path for _method, path, _body in wda.requests if "/source" in path]) == 1


async def test_a_session_s_first_tree_read_cold_is_given_longer_and_the_ones_after_it_are_not(wda: FakeWda) -> None:
    """The first tree of a session is read while the device's accessibility server warms to the app: 5.4 s on an
    iPhone 17 Pro on iOS 27.0 where the next ones took under one. Given up on, a phone that reads fine would be said not
    to -- `sim_device health` said "not ready to drive" -- and WebDriverAgent left building it."""
    assert COLD_SOURCE_TIMEOUT_S > SOURCE_TIMEOUT_S
    client = WdaClient(wda.opener(), probe_timeout_s=0.05)
    reader = WdaReader(client, Orientation(client, SCREEN), SCREEN, timeout_s=0.1, cold_timeout_s=1.0)
    wda.source_delay_s = 0.3
    assert (await reader.accessibility())["backend"] == "wda", "read cold, within its longer wait"
    with pytest.raises(WdaError, match=r"did not build the element tree within 0\.1 s"):
        await reader.accessibility()


async def test_a_touch_is_not_queued_behind_a_tree_webdriveragent_is_still_building(wda: FakeWda) -> None:
    """WebDriverAgent serves one request at a time: a tap sent while it still builds a tree a read gave up on would
    wait behind it and land whenever that is done. It waits a moment for it instead, and is refused unsent after that,
    saying why -- never sent late without the agent knowing."""
    assert INPUT_WAIT_S == SOURCE_TIMEOUT_S
    client = WdaClient(wda.opener(), probe_timeout_s=0.05)
    orientation = Orientation(client, SCREEN)
    reader = WdaReader(client, orientation, SCREEN, timeout_s=0.1)
    await reader.accessibility()
    wda.source_delay_s = 0.6
    with pytest.raises(WdaError, match="did not build the element tree"):
        await reader.accessibility()
    tap = [HidEvent.touch("down", 100, 200), HidEvent.touch("up", 100, 200)]
    with pytest.raises(ConnectorError, match=re.escape(NOT_SENT.format(what="touch"))):
        await WdaInput(client, orientation, wait_s=0.1).hid(stream(tap))
    with pytest.raises(ConnectorError, match="the button press was not sent -- try again in a moment"):
        await WdaInput(client, orientation, wait_s=0).hid(stream(event for _at, event in gestures.button("home")))
    with pytest.raises(ConnectorError, match="the text was not sent"):
        await WdaText(client, wait_s=0).type("milk")
    received = [path for _method, path, _body in wda.requests]
    assert not [path for path in received if path.endswith(("/actions", "/wda/keys", "/wda/homescreen"))]
    # Given long enough, it is sent once the tree is done -- after it, not queued behind it.
    late = asyncio.get_running_loop().create_task(WdaInput(client, orientation, wait_s=3.0).hid(stream(tap)))
    await asyncio.sleep(0.2)
    assert not late.done() and not wda.calls("/actions"), "not sent while the tree is still being built"
    await late
    assert wda.served.index(("GET", "/source")) < wda.served.index(("POST", "/actions"))
    assert len(wda.calls("/actions")) == 1 and client.busy is None


def test_an_alert_or_a_sheet_showing_is_the_modal_in_front() -> None:
    alert = {"type": "Alert", "label": "Allow “Notes” to use your location?", "isVisible": "1"}
    sheet = {"type": "Sheet", "label": None}
    gone = {"type": "Alert", "label": "Gone", "isVisible": "0"}
    app = {"type": "Application", "children": [{"type": "Window", "children": [gone, sheet]}, {"children": [alert]}]}
    assert modal_of(app) == {"type": "Alert", "label": "Allow “Notes” to use your location?"}
    assert modal_of({"type": "Application", "children": [gone, sheet]}) == {"type": "Sheet", "label": None}
    assert modal_of({"type": "Application", "children": [gone, {"type": "Button", "label": "OK"}]}) is None


async def test_the_modal_and_a_list_cut_short_are_said_in_the_document(wda: FakeWda) -> None:
    _sink, reader, _orientation = roles(wda)
    rows = [{"type": "Cell", "label": f"Row {n}", "rect": {"x": 0, "y": 44 * n, "width": 393, "height": 44}}
            for n in range(SOURCE_MAX_CHILDREN)]  # fmt: skip
    alert = {"type": "Alert", "label": "Delete?", "isVisible": "1"}
    wda.source = {"type": "Application", "children": [{"type": "Table", "children": rows}, alert]}
    document = await reader.accessibility()
    assert document["modal"] == {"type": "Alert", "label": "Delete?"}
    (note,) = document["notes"]
    assert note.startswith(f"WebDriverAgent read only the first {SOURCE_MAX_CHILDREN} items of a list")
    assert not cut_short({"children": rows[:-1]}, SOURCE_MAX_CHILDREN)
    tree = tree_from_document(document, source="wda")
    assert tree.modal == Modal("Delete?") and tree.notes == (note,)


async def test_ios_27s_toolbar_around_a_docked_search_field_covers_the_row_it_takes_taps_from(wda: FakeWda) -> None:
    """Settings on iOS 27 (an iPhone 17 Pro, 2026-10-27): WebDriverAgent lists the toolbar the docked search field is
    on as a bare group across the foot of the screen, 126 points tall from 60 above the field. A tap on the row it
    covers -- General, listed visible under the glass -- landed on the toolbar and opened nothing; a row above it
    opened. The group is kept, makes no line, and says where the toolbar is."""
    _sink, reader, _orientation = roles(wda)

    def rect(y: float, height: float, x: float = 16, width: float = 361) -> dict[str, float]:
        return {"x": x, "y": y, "width": width, "height": height}

    toolbar = {"type": "Other", "isVisible": "1", "rect": rect(726, 126, 0, 393)}
    rows = {"type": "Table", "children": [
        {"type": "Button", "label": "Battery", "isVisible": "1", "rect": rect(594, 52)},
        {"type": "Button", "label": "General", "isVisible": "1", "rect": rect(733, 54)},
    ]}  # fmt: skip
    field = {"type": "SearchField", "label": "Search", "isVisible": "1", "rect": rect(786, 28, 28, 337)}
    # As Settings lists it: a toolbar framed as the whole screen, drawing only the field -- and the bare group around.
    whole = {"type": "Toolbar", "label": "Toolbar", "isVisible": "1", "rect": rect(0, 852, 0, 393), "children": [field]}
    expected = [
        'e1 button "Battery" (196,620)',
        'e2 button "General" (196,760) covered by toolbar',
        'e3 search "Search" (196,800)',
    ]
    for listed in ([toolbar, rows, whole], [toolbar, rows, field]):
        wda.source = {"type": "Application", "children": listed}
        tree = tree_from_document(await reader.accessibility(), source="wda")
        lines = [element.line() for element in build(tree, device="", screen=SCREEN, max_elements=50).elements]
        assert lines == expected, listed[-1]["type"]
    wda.source = {"type": "Application", "children": [toolbar, rows, field]}
    document = await reader.accessibility()
    assert len(document["elements"]) == 4, "the bare group is kept for the cover it says, held upright"
    middle = {"type": "Other", "isVisible": "1", "rect": rect(300, 126, 0, 393)}
    assert kept(middle, Turn("PORTRAIT", 393, 852)) == [], "a bare group elsewhere is still left out"
    tall = {"type": "Other", "isVisible": "1", "rect": rect(552, 300, 0, 393)}
    assert kept(tall, Turn("PORTRAIT", 393, 852)) == [], "as is one too tall to be a toolbar"
    assert kept(toolbar, Turn("LANDSCAPE", 393, 852)) == [], "and one at the foot of a screen turned sideways"
