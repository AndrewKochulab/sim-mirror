# SPDX-License-Identifier: Apache-2.0
"""Touches, keys and text through adb, and what is on an Android screen as uiautomator and the window list say."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from typing import Any

import pytest

from sim_mirror.connectors.android.adb_input import AdbInput, AdbText, stroke_command, typed_argument
from sim_mirror.connectors.android.geometry import Rotation
from sim_mirror.connectors.android.keys import ANDROID_BUTTONS, BUTTON_KEYCODES, keycode_of
from sim_mirror.connectors.android.uiautomator import Box, UiAutomatorReader, document, role_of, window_covers
from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.core import gestures
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.fakes import fixture

PIXEL = Screen(1080, 2424, 411, 923, 2.625)
SERIAL = "emulator-5554"


def fixed(quarters: int = 0) -> Rotation:
    async def ask() -> str:
        return f"mCurrentOrientation={quarters}"

    return Rotation(ask)


async def stream(events: Sequence[HidEvent]) -> AsyncIterable[HidEvent]:
    for event in events:
        yield event


@pytest.fixture
async def server() -> AsyncIterator[FakeAdbServer]:
    async with FakeAdbServer() as running:
        yield running.emulator()


def test_buttons_and_keys_are_androids_keycodes() -> None:
    assert ANDROID_BUTTONS == ("back", "home", "app_switch", "lock", "volume_up", "volume_down")
    assert [BUTTON_KEYCODES[button] for button in ANDROID_BUTTONS] == [4, 3, 187, 26, 24, 25]
    assert keycode_of(gestures.KEYS["return"]) == 66 and keycode_of(gestures.KEYS["delete"]) == 67
    assert keycode_of(gestures.CHARACTER_KEYS["a"][0]) == 29 and keycode_of(gestures.CHARACTER_KEYS["z"][0]) == 54
    assert keycode_of(gestures.CHARACTER_KEYS["1"][0]) == 8 and keycode_of(gestures.CHARACTER_KEYS["0"][0]) == 7
    assert keycode_of(gestures.CHARACTER_KEYS["."][0]) == 56 and keycode_of(240) is None


def test_a_stroke_is_a_tap_where_it_barely_moved_else_a_swipe_as_long_as_it_took() -> None:
    assert stroke_command(PIXEL, 0, [(0.0, 100, 200), (0.1, 102, 201)]) == ["input", "tap", "262", "525"]
    assert stroke_command(PIXEL, 0, [(0.0, 100, 800), (0.25, 100, 500), (0.3, 100, 200)]) == [
        "input", "swipe", "262", "2100", "262", "525", "300",
    ]  # fmt: skip
    # A long press stays put, and is a swipe that does not move.
    assert stroke_command(PIXEL, 0, [(0.0, 100, 200), (0.8, 100, 200)]) == [
        "input", "swipe", "262", "525", "262", "525", "800",
    ]  # fmt: skip
    # Turned a quarter, the portrait screen's points are the display's turned ones.
    assert stroke_command(PIXEL, 1, [(0.0, 0, 0), (0.05, 0, 0)]) == ["input", "tap", "0", "1079"]


async def test_touches_buttons_and_keys_are_sent_as_input_commands(server: FakeAdbServer) -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 0.1
        return now[0]

    sink = AdbInput(server.adb(), SERIAL, PIXEL, fixed(), clock=clock)
    await sink.hid(stream([event for _at, event in gestures.tap(100, 200)]))
    await sink.hid(stream([event for _at, event in gestures.button("back")]))
    await sink.hid(stream([event for _at, event in gestures.key("return")]))
    await sink.hid(stream([event for _at, event in gestures.select_all()]))
    assert server.commands() == [
        "input tap 262 525",
        "input keyevent 4",
        "input keyevent 66",
        "input keycombination 113 29",
    ]


async def test_what_android_has_no_key_for_is_refused_and_a_failure_says_so(server: FakeAdbServer) -> None:
    sink = AdbInput(server.adb(), SERIAL, PIXEL, fixed())
    with pytest.raises(ConnectorError, match="no siri button"):
        await sink.hid(stream([HidEvent.press("siri", "down")]))
    with pytest.raises(ConnectorError, match="key 240 has no Android key"):
        await sink.hid(stream([HidEvent.key(240, "down")]))
    server.on("input", code=1, err="Error: injection denied\n")
    with pytest.raises(ConnectorError, match="did not take the input: Error: injection denied"):
        await sink.hid(stream([HidEvent.press("home", "down")]))


async def test_ascii_text_is_typed_whole_and_anything_else_needs_scrcpy(server: FakeAdbServer) -> None:
    text = AdbText(server.adb(), SERIAL)
    await text.type("hello world & more")
    assert server.commands()[-1] == "input text 'hello%sworld%s&%smore'"
    assert typed_argument("a b") == "a%sb"
    for refused in ("café", "line\nbreak"):
        with pytest.raises(ConnectorError, match="set scrcpy up"):
            typed_argument(refused)
    server.on("input text", code=1, err="boom\n")
    with pytest.raises(ConnectorError, match="did not take the text: boom"):
        await text.type("x")


def test_each_view_has_the_role_readers_speak() -> None:
    from xml.etree.ElementTree import Element

    def node(cls: str, **attributes: str) -> Element:
        return Element("node", {"class": cls, **attributes})

    assert role_of(node("android.widget.EditText")) == "TextField"
    assert role_of(node("android.widget.EditText", password="true")) == "SecureTextField"
    assert role_of(node("androidx.appcompat.widget.SwitchCompat")) == "Switch"
    assert role_of(node("android.widget.TextView", clickable="true")) == "Button"
    assert role_of(node("android.widget.ImageView", **{"long-clickable": "true"})) == "Button"
    assert role_of(node("android.view.View", checkable="true")) == "Toggle"
    assert role_of(node("android.view.View", scrollable="true")) == "ScrollView"
    assert role_of(node("android.view.View")) == "Group"
    assert role_of(node("com.google.android.material.tabs.TabLayout$TabView")) == "Tab"


def walk(elements: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [found for element in elements for found in (element, *walk(element["children"]))]


def test_settings_reads_as_its_rows_by_what_they_say_with_the_status_bar_and_navigation_covering() -> None:
    read = document(fixture("android-uiautomator-settings.xml"), fixture("android-window-settings.txt"), PIXEL)
    assert (read["backend"], read["orientation"], read["screen"]) == (
        "uiautomator", "portrait", {"coordinate_space": "screen", "width": 411, "height": 923},
    )  # fmt: skip
    elements = walk(read["elements"])
    labels = [element["label"] for element in elements if element["type"] == "Button"]
    assert "Network & internet, Mobile, Wi‑Fi, hotspot" in labels
    assert not any(element["type"] == "StaticText" and element["label"] == "Network & internet" for element in elements)
    covers = {element["type"]: element["frame"] for element in elements if element["type"] in ("StatusBar", "Toolbar")}
    assert covers["StatusBar"] == {"x": 0.0, "y": 0.0, "width": 411.43, "height": 54.1}
    assert covers["Toolbar"]["y"] == pytest.approx(899.43, abs=0.01)


def test_a_focused_field_is_editing_and_the_keyboard_covers_where_it_takes_touches() -> None:
    read = document(fixture("android-uiautomator-search.xml"), fixture("android-window-search.txt"), PIXEL)
    elements = walk(read["elements"])
    field = next(element for element in elements if element["type"] == "TextField")
    assert field["label"] == "Search settings" and "IsEditing" in field["traits"] and field["value"] is None
    keyboard = next(element for element in elements if element["type"] == "Keyboard")
    assert keyboard["frame"]["y"] == pytest.approx(1541 / 2.625, abs=0.01)


def test_a_turned_screen_is_read_as_an_interface_turned_that_way() -> None:
    read = document(fixture("android-uiautomator-landscape.xml"), "", PIXEL)
    assert read["orientation"] == "landscape_right" and read["screen"]["width"] == 923


def test_a_dump_that_failed_or_is_not_xml_says_so() -> None:
    with pytest.raises(ConnectorError, match="could not get idle state"):
        document("ERROR: could not get idle state.\n", "", PIXEL)
    with pytest.raises(ConnectorError, match="it said nothing"):
        document("", "", PIXEL)
    with pytest.raises(ConnectorError, match="is not XML"):
        document("<?xml version='1.0'?><hierarchy rotation=\"0\"><node></hierarchy>", "", PIXEL)


def test_values_states_and_odd_bounds_are_read_as_they_are() -> None:
    dump = (
        '<?xml version="1.0"?><hierarchy rotation="x">'
        '<node class="android.widget.Switch" text="Wi-Fi" checkable="true" checked="true" bounds="[0,0][100,50]"/>'
        '<node class="android.widget.EditText" text="ada" hint="Name" bounds="[0,60][100,110]"/>'
        '<node class="android.widget.TextView" text="Gone" bounds="[0,0][0,0]"/>'
        '<node class="android.widget.TextView" text="Crossed" bounds="[10,200][90,150]"/>'
        '<node class="android.widget.Button" text="Off" enabled="false" selected="true" bounds=""/>'
        "</hierarchy>"
    )
    elements = walk(document(dump, "", Screen(100, 200, 100, 200, 1.0))["elements"])
    by_label = {element["label"]: element for element in elements}
    assert by_label["Wi-Fi"]["value"] == "1" and "Selected" in by_label["Wi-Fi"]["traits"]
    assert by_label["Name"]["value"] == "ada"
    assert "Gone" not in by_label and by_label["Crossed"]["frame"] == {"x": 10, "y": 150, "width": 80, "height": 50}
    assert "Off" not in by_label, "a view with no bounds is not drawn"


def test_only_windows_drawn_now_cover() -> None:
    dumped = (
        "  Window #0 Window{a u0 InputMethod}:\n    ty=INPUT_METHOD\n    Frames: frame=[0,1000][1080,2424]\n"
        "    isVisible=true\n"
        "  Window #1 Window{b u0 StatusBar}:\n    ty=STATUS_BAR\n    isVisible=false\n"
        "  Window #2 Window{c u0 Taskbar}:\n    ty=NAVIGATION_BAR\n    isVisible=true\n"
        "  Window #3 Window{d u0 Other}:\n    ty=BASE_APPLICATION\n    isVisible=true\n"
        "  Window #4 Window{e u0 Empty}:\n    ty=STATUS_BAR\n    Frames: frame=[0,0][0,0]\n    isVisible=true\n"
    )
    assert window_covers(dumped) == [("Keyboard", "Keyboard", Box(0, 1000, 1080, 2424))]


async def test_the_reader_reads_the_dump_and_the_windows_together(server: FakeAdbServer) -> None:
    read = await UiAutomatorReader(server.adb(), SERIAL, PIXEL).accessibility()
    assert read["backend"] == "uiautomator" and read["elements"]
    with pytest.raises(ConnectorError, match="could not be read through uiautomator"):
        await UiAutomatorReader(server.adb(), "emulator-9", PIXEL).accessibility()
