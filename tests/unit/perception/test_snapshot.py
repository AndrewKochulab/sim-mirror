# SPDX-License-Identifier: Apache-2.0
"""The screen as an agent reads it: a line per element, refs that follow an element, a digest, and what changed --
the same text, byte for byte, as the implementation SimMirror's snapshots were first measured with (golden/)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, ScreenTree
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import (
    LABEL_MAX,
    NOTHING_READ,
    NOTHING_SEEN,
    LineParts,
    Snapshot,
    build,
    diff,
    line_parts,
    says_anything,
)
from sim_mirror.testing.fakes import SCREEN, fixture_json

DEVICE = "iOS 26.5"
GOLDEN = Path(__file__).parent / "golden"


def snap(
    doc: dict[str, Any], *, max_elements: int = 120, previous: Snapshot | None = None, screen: Screen = SCREEN
) -> Snapshot:
    return build(tree_from_document(doc), device=DEVICE, screen=screen, max_elements=max_elements, previous=previous)


def golden(name: str) -> str:
    return (GOLDEN / f"{name}.txt").read_text(encoding="utf-8").removesuffix("\n")


@pytest.mark.parametrize("name", ["ax-settings-interactable", "ax-safari-typed", "ax-home-complete"])
def test_every_fixture_reads_exactly_as_the_golden_text(name: str) -> None:
    assert snap(fixture_json(f"{name}.json")).text() == golden(name)


def test_a_capped_screen_and_a_diff_read_exactly_as_the_golden_text() -> None:
    settings = fixture_json("ax-settings-interactable.json")
    assert snap(settings, max_elements=10).text() == golden("settings-capped-10")
    first = snap(settings)
    changed = copy.deepcopy(settings)
    children = changed["elements"][0]["children"]
    next(node for node in children if node.get("label") == "General")["frame"]["y"] += 100
    children[:] = [node for node in children if node.get("label") != "Camera"]
    assert diff(first, snap(changed, previous=first)) == golden("settings-changed-diff")


def node_named(doc: dict[str, Any], label: str) -> dict[str, Any]:
    stack = list(doc["elements"])
    while stack:
        node = stack.pop()
        if node.get("label") == label:
            return node
        stack.extend(node.get("children") or [])
    raise AssertionError(label)


def test_settings_reads_as_a_heading_and_a_line_per_thing_to_touch() -> None:
    doc = fixture_json("ax-settings-interactable.json")
    snapshot = snap(doc)
    lines = snapshot.text().splitlines()
    assert lines[0] == f"{DEVICE} · Settings · 402x874pt · #{snapshot.digest[:4]}" and len(snapshot.digest) == 16
    assert lines[1] == '[heading "Settings"]'
    assert lines[2].startswith('e1 button "Apple Account, Sign in') and '…"' in lines[2]
    assert len(lines[2].split('"')[1]) == LABEL_MAX
    assert 'e2 button "General" (201,319)' in lines
    assert 'e12 search ="Search" (201,822)' in lines
    assert 'e13 button "Dictate" (344,822)' in lines
    passcode = snapshot.find("Passcode")
    frame = node_named(doc, "Passcode")["frame"]
    assert passcode is not None and passcode.ref == "e11" and passcode.y == (frame["y"] + SCREEN.height_pt) / 2
    assert len(lines) == 15 and snapshot.next_ref == 14
    general = snapshot.element("e2")
    assert general is not None and general.label == "General" and snapshot.element("e99") is None
    assert snapshot.find("GENERAL").ref == "e2" and snapshot.find("search").ref == "e8"  # type: ignore[union-attr]
    assert snapshot.find("bluetooth") is None


def test_safari_keeps_a_fields_text_what_is_selected_or_editing_and_tells_same_named_buttons_apart() -> None:
    snapshot = snap(fixture_json("ax-safari-typed.json"))
    text = snapshot.text()
    assert snapshot.header.startswith(f"{DEVICE} · Safari · ")
    assert 'e1 field "Address" ="цшаш café 😀" (157,816) editing' in text
    assert '[heading "Google Suggestions"]' in text
    assert 'text "цшаш café 😀" (201,109) selected' in text
    completes = [element.ref for element in snapshot.elements if element.label == "Complete search"]
    assert len(completes) == 3 and len(set(completes)) == 3


def test_a_heading_said_twice_is_one_line_and_two_notes_with_the_same_title_stay_two() -> None:
    def text(label: str, y: int) -> dict[str, Any]:
        return {"type": "StaticText", "label": label, "frame": {"x": 20, "y": y, "width": 200, "height": 30}}

    heading = {"type": "Heading", "label": "Notes", "frame": {"x": 20, "y": 60, "width": 200, "height": 40}}
    doc = {"elements": [{"type": "Application", "label": "NotesProbe", "children": [
        {"type": "NavigationBar", "children": [heading]}, {**heading, "frame": {**heading["frame"], "y": 120}},
        text("Trip", 300), text("Trip", 360)]}]}  # fmt: skip
    lines = snap(doc).text().splitlines()
    assert lines.count('[heading "Notes"]') == 1
    assert [line for line in lines if '"Trip"' in line] == ['e1 text "Trip" (120,315)', 'e2 text "Trip" (120,375)']


def test_a_screen_with_nothing_readable_says_so_and_what_still_shows_it() -> None:
    doc = {"elements": [{"type": "Application", "label": None, "frame": {"x": 0, "y": 0, "width": 0, "height": 0}}]}
    lines = snap(doc).text().splitlines()
    assert lines[0].startswith(f"{DEVICE} · 402x874pt · #")
    assert lines[1].startswith("nothing on this screen could be read") and "sim_screenshot still shows" in lines[1]
    assert len(lines) == 2
    assert "nothing on this screen" not in snap(fixture_json("ax-settings-interactable.json")).text()


def test_a_nameless_app_is_left_out_of_the_header() -> None:
    snapshot = snap(fixture_json("ax-home-complete.json"))
    assert snapshot.header == f"{DEVICE} · 402x874pt · #{snapshot.digest[:4]}"
    assert 'e1 search ="App Library" (201,114)' in snapshot.text()


def test_refs_follow_their_element_as_the_screen_changes_and_are_never_given_to_another() -> None:
    doc = fixture_json("ax-settings-interactable.json")
    first = snap(doc)
    changed = copy.deepcopy(doc)
    children = changed["elements"][0]["children"]
    general = next(node for node in children if node.get("label") == "General")
    general["frame"]["y"] += 100
    children[:] = [node for node in children if node.get("label") != "Camera"]
    children.append({"type": "Button", "label": "Bluetooth", "identifier": "bt", "frame": {
        "x": 16, "y": 200, "width": 370, "height": 52}, "enabled": True, "traits": ["Button"]})  # fmt: skip
    second = snap(changed, previous=first)
    moved = second.element("e2")
    assert moved is not None and moved.label == "General" and round(moved.y) == 419
    assert second.find("Bluetooth").ref == "e14" and second.element("e6") is None  # type: ignore[union-attr]
    changes = diff(first, second).splitlines()
    assert changes[0] == f"#{first.digest[:4]} → #{second.digest[:4]}"
    assert '+ e14 button "Bluetooth" (201,226)' in changes
    assert '~ e2 button "General" (201,419)' in changes
    assert '- e6 button "Camera" (201,527)' in changes
    third = snap(doc, previous=second)
    assert third.find("Camera").ref == "e15"  # type: ignore[union-attr]


def test_look_alikes_keep_their_refs_as_they_move_and_are_given_fresh_ones_when_one_is_added_or_removed() -> None:
    def button(y: int, label: str = "Delete") -> dict[str, Any]:
        return {"type": "Button", "label": label, "frame": {"x": 20, "y": y, "width": 200, "height": 40}}

    def screen(*buttons: dict[str, Any]) -> dict[str, Any]:
        return {"elements": [{"type": "Application", "label": "Notes", "children": list(buttons)}]}

    first = snap(screen(button(300), button(360), button(500, "Save")))
    assert [element.ref for element in first.elements] == ["e1", "e2", "e3"]
    moved = snap(screen(button(320), button(380), button(520, "Save")), previous=first)
    assert [element.ref for element in moved.elements] == ["e1", "e2", "e3"]
    added = snap(screen(button(240), button(320), button(380), button(520, "Save")), previous=moved)
    assert [element.ref for element in added.elements] == ["e5", "e6", "e4", "e3"]
    assert added.element("e1") is None and added.element("e2") is None
    assert added.element("e3").label == "Save"  # type: ignore[union-attr]
    removed = snap(screen(button(320), button(380), button(520, "Save")), previous=added)
    assert [element.ref for element in removed.elements] == ["e7", "e8", "e3"]


def test_nothing_changed_says_so_and_a_screen_that_all_moved_is_given_whole() -> None:
    doc = fixture_json("ax-settings-interactable.json")
    first = snap(doc)
    assert diff(first, snap(doc, previous=first)) == f"no change · #{first.digest[:4]}"
    scrolled = copy.deepcopy(doc)
    for node in scrolled["elements"][0]["children"]:
        node["frame"]["y"] -= 40
    moved = snap(scrolled, previous=first)
    assert diff(first, moved) == moved.text()


def test_headings_that_come_and_go_are_in_the_diff() -> None:
    def screen(heading: str) -> dict[str, Any]:
        return {"elements": [
            {"type": "Heading", "label": heading, "frame": {"x": 0, "y": 0, "width": 100, "height": 40}},
            *({"type": "Button", "label": f"Row {n}", "frame": {"x": 0, "y": 50 + 50 * n, "width": 300, "height": 40}}
              for n in range(12)),
        ]}  # fmt: skip

    before = snap(screen("Inbox"))
    changes = diff(before, snap(screen("Drafts"), previous=before))
    assert changes.splitlines()[1:] == ['+ [heading "Drafts"]', '- [heading "Inbox"]']


def test_what_is_off_screen_empty_or_a_container_is_left_out_and_the_rest_is_capped() -> None:
    elements = [
        {"type": "Group", "label": "wrapper", "frame": {"x": 0, "y": 0, "width": 402, "height": 874}, "children": [
            {"type": "Button", "label": "Below", "frame": {"x": 0, "y": 900, "width": 50, "height": 50}},
            {"type": "Button", "label": "Left", "frame": {"x": -80, "y": 10, "width": 50, "height": 50}},
            {"type": "Image", "frame": {"x": 0, "y": 10, "width": 50, "height": 50}},
            {"type": "SecureTextField", "frame": {"x": 0, "y": 100, "width": 200, "height": 40}},
            {"type": "Switch", "label": "Wi-Fi", "value": "1", "enabled": False, "frame": {"x": 0, "y": 150,
                                                                                         "width": 60, "height": 30}},
            {"type": "Map", "label": "Map of Kyiv", "frame": "not a frame"},
            {"type": "Slider", "title": "Volume", "value": "Volume", "frame": {"x": 0, "y": 200, "width": 200,
                                                                              "height": 30}},
            "not an element",
        ]},
    ]  # fmt: skip
    snapshot = snap(
        {"elements": elements, "modal": {"type": "Alert", "label": "Allow “Notes” to use your location?"},
         "truncated": True},
        max_elements=2,
    )  # fmt: skip
    assert [element.line() for element in snapshot.elements] == [
        "e1 secure (100,120)",
        'e2 switch "Wi-Fi" ="1" (30,165) disabled',
    ]
    assert snapshot.notes == (
        "… 1 more not shown",
        "a modal is in front: Allow “Notes” to use your location?",
        "the companion cut this tree short",
    )
    whole = snap({"elements": elements, "modal": True}, max_elements=10)
    assert 'e3 slider "Volume" (100,215)' in whole.text() and "a modal is in front" in whole.notes
    assert snap({"elements": None}).elements == () and snap({}).header.endswith("pt · #" + snap({}).digest[:4])
    odd = snap(
        {"elements": [{"type": "Gauge", "label": "Battery", "frame": {"x": 0, "y": 0, "width": 10, "height": 10}}]},
        screen=Screen(100, 100, 50, 50, 2.0),
    )
    assert odd.elements[0].line() == 'e1 gauge "Battery" (5,5)'
    nameless = snap({"elements": [{"label": "?", "frame": {"x": 0, "y": 0, "width": 10, "height": 10}}]})
    assert nameless.elements[0].kind == "element"


def test_lines_read_from_pixels_are_counted_and_a_screen_whose_pixels_said_nothing_says_that() -> None:
    text = ElementNode(role="StaticText", label="Sign in", frame=Frame(20, 100, 80, 20), source=PIXELS)
    button = ElementNode(role="Button", label="Help", frame=Frame(20, 200, 80, 40), source="idb")
    one = build(ScreenTree(roots=(text, button), pixels=True), device=DEVICE, screen=SCREEN, max_elements=10)
    assert one.text().splitlines()[1:] == [
        'e1 text "Sign in" (60,110)',
        'e2 button "Help" (60,220)',
        "1 line was read from the screen's pixels: text may be misread, and a ref taps its middle",
    ]
    two = build(ScreenTree(roots=(text, text), pixels=True), device=DEVICE, screen=SCREEN, max_elements=10)
    assert two.notes == ("2 lines were read from the screen's pixels: text may be misread, and a ref taps its middle",)
    capped = build(ScreenTree(roots=(button, text), pixels=True), device=DEVICE, screen=SCREEN, max_elements=1)
    assert capped.notes == ("… 1 more not shown",)
    blank = build(ScreenTree(pixels=True), device=DEVICE, screen=SCREEN, max_elements=10)
    assert blank.notes == (NOTHING_SEEN,) and "not even text in its pixels" in NOTHING_SEEN
    assert build(ScreenTree(), device=DEVICE, screen=SCREEN, max_elements=10).notes == (NOTHING_READ,)


def test_whether_a_tree_says_anything_is_whether_its_snapshot_would_have_a_line() -> None:
    off_screen = ElementNode(role="Button", label="Far", frame=Frame(0, 2000, 10, 10))
    container = ElementNode(role="Group", label="Box", frame=Frame(0, 0, 10, 10), children=(off_screen,))
    empty_field = ElementNode(role="TextField", frame=Frame(0, 0, 100, 40))
    assert not says_anything(ScreenTree(roots=(container,)), SCREEN)
    assert says_anything(ScreenTree(roots=(container, empty_field)), SCREEN)
    assert line_parts(empty_field, SCREEN) == LineParts("field", "", None, (50.0, 20.0))
    same = ElementNode(role="StaticText", label="7", value="7", frame=Frame(0, 0, 10, 10))
    assert line_parts(same, SCREEN) == LineParts("text", "7", None, (5.0, 5.0))
