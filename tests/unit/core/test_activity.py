# SPDX-License-Identifier: Apache-2.0
"""What an agent is doing, said briefly for a person watching: a gesture, a tool call, a wait."""

from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.core.activity import QUOTED_MAX, of_call, of_gesture, of_wait, quoted


def test_a_quote_is_one_line_cut_to_fit() -> None:
    assert quoted("Add") == "“Add”"
    assert quoted("  Add\n item ") == "“Add item”"
    long = quoted("abcdefghijklmnopqrstuvwxyz0123456789")
    assert long == "“abcdefghijklmnopqrstuvw…”" and len(long) == QUOTED_MAX + 2
    assert quoted("a very long label that is cut at a space") == "“a very long label that…”"


@pytest.mark.parametrize(
    ("kind", "fields", "said"),
    [
        ("tap", {"label": "Add"}, "Tapping “Add”"),
        ("tap", {}, "Tapping"),
        ("tap", {"label": "Add", "mac": True}, "Clicking “Add”"),
        ("long_press", {"label": "Row"}, "Pressing and holding “Row”"),
        ("long_press", {"label": "Row", "mac": True}, "Holding a click on “Row”"),
        ("right_click", {"label": "File", "mac": True}, "Right-clicking “File”"),
        ("hover", {"label": "Help"}, "Pointing at “Help”"),
        ("scroll", {}, "Scrolling"),
        ("drag", {"label": "Card"}, "Dragging “Card”"),
        ("swipe", {"points": [(200, 700), (200, 200)]}, "Swiping up"),
        ("swipe", {"points": [(200, 200), (210, 700)]}, "Swiping down"),
        ("swipe", {"points": [(300, 400), (50, 420)]}, "Swiping left"),
        ("swipe", {"points": [(50, 400), (300, 380)]}, "Swiping right"),
        ("swipe", {"points": [(50, 400)]}, "Swiping"),
        ("type", {"caption": "Groceries"}, "Typing “Groceries”"),
        ("type", {"caption": " "}, "Typing…"),
        ("press", {"caption": "home"}, "Pressing home"),
        ("keys", {"caption": "cmd+s"}, "Pressing cmd+s"),
        ("keys", {}, "Pressing a key"),
        ("menu", {"caption": "File > Save"}, "Choosing File > Save"),
        ("menu", {}, "Choosing from the menu"),
        ("look", {}, "Reading the screen"),
        ("pause", {}, "Waiting"),
        ("app", {"caption": "launch com.example.Todo"}, "Launching com.example.Todo"),
        ("app", {"caption": "relaunch com.example.Todo"}, "Relaunching com.example.Todo"),
        ("app", {"caption": "quit com.example.Todo"}, "Quitting com.example.Todo"),
        ("app", {"caption": "install Todo.app"}, "Installing Todo.app"),
        ("app", {"caption": "open https://example.com"}, "Opening https://example.com"),
        ("device", {"caption": "switched to iPhone 17"}, "Switching to iPhone 17"),
        ("device", {"caption": "turned to landscape_left"}, "Turning to landscape_left"),
        ("device", {"caption": "appearance dark"}, "Appearance dark"),
        ("build", {"label": "Todo"}, "“Todo”"),
        ("build_run", {}, "Build run"),
    ],
)
def test_a_gesture_says_what_it_does_and_what_it_lands_on(kind: str, fields: dict[str, Any], said: str) -> None:
    assert of_gesture(kind, **fields) == said


@pytest.mark.parametrize(
    ("name", "arguments", "said"),
    [
        ("sim_snapshot", {}, "Reading the screen"),
        ("sim_screenshot", {"frames": 3}, "Looking at the screen"),
        ("sim_build_run", {}, "Building"),
        ("sim_test", {}, "Running tests"),
        ("sim_act", {"steps": []}, ""),
        ("sim_app", {"action": "launch"}, "Launching the app"),
        ("sim_app", {"action": "logs"}, "Reading the logs"),
        ("sim_app", {"action": "something"}, "Checking the app"),
        ("sim_app", None, "Checking the app"),
        ("sim_device", {"action": "boot"}, "Starting the device"),
        ("sim_device", {"action": 3}, "Checking the device"),
        ("sim_record", {"action": "start"}, "Recording the screen"),
        ("sim_record", {"action": "stop"}, "Saving the recording"),
        ("sim_unknown", {}, ""),
        (7, {}, ""),
    ],
)
def test_a_tool_call_says_what_it_does_while_it_runs(name: object, arguments: object, said: str) -> None:
    assert of_call(name, arguments) == said


@pytest.mark.parametrize(
    ("spec", "said"),
    [
        ({"for": "Saved"}, "Waiting for “Saved”"),
        ({"for": {"exact": "Locked"}}, "Waiting for “Locked”"),
        ({"for": {"regex": "^Lock"}}, "Waiting for “^Lock”"),
        ({"for": {"text": "Done", "case": True}}, "Waiting for “Done”"),
        ({"for": {"case": True}}, "Waiting for “text”"),
        ({"gone": "Loading"}, "Waiting for “Loading” to go"),
        ({"stable": {"for": "Ready", "ms": 3000}}, "Waiting for “Ready” to stay"),
        ({"stable": {"gone": "Spinner", "ms": 3000}}, "Waiting for “Spinner” to stay away"),
        ({"settle_ms": 400}, "Waiting for the screen to settle"),
        ("soon", "Waiting"),
    ],
)
def test_a_wait_says_what_it_waits_for(spec: object, said: str) -> None:
    assert of_wait(spec) == said
