# SPDX-License-Identifier: Apache-2.0
"""Xcode's UI hierarchy text, as Xcode 27.0 wrote it, read into a companion's document."""

from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.connectors.base import Screen
from sim_mirror.connectors.mcpbridge import hierarchy
from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import build
from sim_mirror.testing.fakes import fixture

SCREEN = Screen(1206, 2622, 402, 874, 3.0)


def lines(name: str, max_elements: int = 120) -> list[str]:
    tree = tree_from_document(document_from_hierarchy(fixture(f"mcpbridge-hierarchy-{name}.txt")), source="mcpbridge")
    return build(tree, device="iOS 27.0", screen=SCREEN, max_elements=max_elements).text().splitlines()[1:]


def one(text: str) -> dict[str, Any]:
    """The only element under the application a hierarchy of one line has."""
    document = document_from_hierarchy("Application, pid: 1, label: 'App'\n" + text)
    (app,) = document["elements"]
    (element,) = app["children"]
    return element


def test_labels_keep_their_quotes_commas_line_breaks_and_words_that_look_like_keys() -> None:
    assert lines("probe") == [
        "e1 text \"It's a 'quote', with commas, and: colons\" (165,88)",
        'e2 text "Line one Line two" (48,131)',
        'e3 text "Emoji 😀 and café, Київ" (106,175)',
        "e4 text \"label: 'fake', identifier: 'fake', hitPoint: {1.0, 2.0}\" (191,207)",
        'e5 switch "Switch on" ="1" (201,243)',
        'e6 switch "Switch off" ="0" (201,283)',
        'e7 slider ="25%" (201,325)',
        'e8 field "Name placeholder" ="it\'s, "quoted", done" (201,369)',
        'e9 field "Empty placeholder" (201,415)',
        'e10 secure "Password" ="•••••••" (201,461)',
        'e11 button "Disabled button" (76,500) disabled',
        'e12 button "Show alert" (56,533)',
        'e13 text "A long value that goes on A long value that goes on A long …" (201,598)',
        'e14 image "Favorite" (27,778)',
        'e15 text "Labelled star" (81,811)',
    ]


def test_a_line_break_in_a_label_stays_in_it() -> None:
    document = document_from_hierarchy(fixture("mcpbridge-hierarchy-probe.txt"))
    labels = [node.label for node in tree_from_document(document).walk()]
    assert "Line one\nLine two" in labels


def test_web_content_reads_once_each_and_an_alert_is_the_modal_in_front() -> None:
    assert lines("safari")[:3] == [
        'e1 text "Example Domain" (174,190)',
        'e2 text "This domain is for use in documentation examples without ne…" (200,260)',
        'e3 link "Learn more" (122,326)',
    ]
    alert = document_from_hierarchy(fixture("mcpbridge-hierarchy-alert.txt"))
    assert alert["modal"] == {"type": "Alert", "label": "Alert title, with 'quotes'"}
    assert lines("alert")[-1] == "a modal is in front: Alert title, with 'quotes'"


def test_what_the_keyboard_covers_is_left_out_and_the_field_being_typed_in_says_so() -> None:
    placeless = document_from_hierarchy(
        "Application, pid: 1, label: 'App'\n"
        " Button, {{nowhere}}, label: 'Somewhere'\n"
        " Keyboard, {{0.0, 500.0}, {400.0, 300.0}}\n"
    )
    assert [child["type"] for child in placeless["elements"][0]["children"]] == ["Button", "Keyboard"]
    read = lines("keyboard")
    assert 'e9 field "Empty placeholder" (201,415) editing' in read
    assert not any("A long value" in line or "Favorite" in line for line in read)
    assert 'e13 key "Q" (24,624)' in read


def test_app_icons_are_buttons_and_the_status_bar_and_widgets_are_read() -> None:
    read = lines("home")
    assert 'e7 button "Photos" (155,334)' in read
    assert 'e19 text "23:15" (74,33)' in read and 'e20 image "Wi-Fi" (323,33)' in read


def test_text_inside_a_button_and_a_symbols_own_name_are_not_said_again() -> None:
    read = lines("settings")
    assert read[:3] == [
        'e1 text "Settings" (82,140)',
        'e2 button "Apple Account, Sign in to access your iCloud data, the App …" (201,213)',
        'e3 button "Optimizing Search and Siri" (201,319)',
    ]
    assert not any(line.split(" ", 1)[1].startswith(('text "General"', "image")) for line in read)


def test_an_element_line_reads_its_frame_keys_and_flags() -> None:
    element = one(
        "  Button, {{1.0, 2.5}, {30.0, 40.0}}, identifier: 'save', label: 'Save', value: on, Selected, Disabled, "
        "hitPoint: {16.0, 22.5}, activationBundleId: com.example.app"
    )
    assert element == {
        "type": "Button",
        "label": "Save",
        "identifier": "save",
        "title": "",
        "value": "on",
        "frame": {"x": 1.0, "y": 2.5, "width": 30.0, "height": 40.0},
        "enabled": False,
        "traits": ["Selected"],
        "children": [],
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # An element off screen has no hit point, and a value may carry commas and words like flags.
        (" Icon, {{426.0, 88.0}, {68.0, 90.7}}, identifier: 'Maps', label: 'Maps', value: Widget, Stack",
         {"type": "Button", "label": "Maps", "value": "Widget, Stack"}),
        # An identifier ends at the first key after it; a label runs to the last.
        (" StaticText, {{0.0, 0.0}, {1.0, 1.0}}, identifier: 'a', label: 'x', label: 'y', hitPoint: {0.5, 0.5}",
         {"identifier": "a", "label": "x', label: 'y"}),
        (" TextField, {{0.0, 0.0}, {1.0, 1.0}}, placeholderValue: 'Name', value: it's, Focused, hitPoint: {0.5, 0.5}",
         {"title": "Name", "value": "it's", "traits": ["Focused"]}),
        (" Image, {{0.0, 0.0}, {1.0, 1.0}}, identifier: 'chevron.forward', label: 'chevron.forward'",
         {"label": "", "identifier": "chevron.forward"}),
        (" StaticText, {{0.0, 0.0}, {1.0, 1.0}}, label: 'A long text here', value: A long te...",
         {"label": "A long text here", "value": ""}),
        (" Other, {{0.0, 0.0}, {inf, 1.0}}, label: 'Unbounded'", {"frame": None, "label": "Unbounded"}),
        (" Other, {{0.0, 0.0}, {1.0, 1.0}}, isRemoteLeafPlaceholder", {"label": "", "enabled": True, "traits": []}),
        (" Other, {{nowhere}}, label: 'odd'", {"frame": None, "label": ""}),
    ],
)  # fmt: skip
def test_a_line_reads_as_xcode_wrote_it(text: str, expected: dict[str, Any]) -> None:
    element = one(text)
    assert {key: element[key] for key in expected} == expected


def test_sections_headers_blank_lines_and_what_comes_before_any_element_are_not_elements() -> None:
    document = document_from_hierarchy(
        "Device orientation: Unknown\n"
        "stray words\n"
        "------------------------\n"
        "Application bundle identifier: com.apple.springboard\n"
        "Application, pid: 1, label: ' '\n"
        " Window, {{0.0, 0.0}, {402.0, 874.0}}, hitPoint: {201.0, 437.0}\n"
        "\n"
        "------------------------\n"
        "Application bundle identifier: com.example.app\n"
        "Application, pid: 2, label: 'App'\n"
    )
    apps = document["elements"]
    assert [(app["label"], len(app["children"])) for app in apps] == [(" ", 1), ("App", 0)]
    assert document == {"elements": apps, "modal": None, "truncated": False}


def test_a_label_that_breaks_before_a_header_like_line_keeps_it() -> None:
    element = one(" StaticText, {{0.0, 0.0}, {1.0, 1.0}}, label: 'Price\nTotal: 5', hitPoint: {0.5, 0.5}")
    assert element["label"] == "Price\nTotal: 5"


def test_an_element_written_twice_in_the_same_place_is_read_once_and_whatever_it_held_stays() -> None:
    document = document_from_hierarchy(
        "Application, pid: 1, label: 'App'\n"
        " Button, {{0.0, 0.0}, {10.0, 10.0}}, label: 'Dictate'\n"
        " Button, {{0.0, 0.0}, {10.0, 10.0}}, label: 'Dictate'\n"
        "  StaticText, {{0.0, 20.0}, {10.0, 10.0}}, label: 'Held'\n"
        " Switch, {{0.0, 40.0}, {10.0, 10.0}}, label: 'Wi-Fi'\n"
        "  Switch, {{5.0, 40.0}, {5.0, 10.0}}, value: 1\n"
        "  Switch, {{5.0, 40.0}, {5.0, 10.0}}, identifier: 'inner', value: 1\n"
    )
    (app,) = document["elements"]
    assert [(child["label"], child["identifier"]) for child in app["children"]] == [
        ("Dictate", ""),
        ("Held", ""),
        ("Wi-Fi", ""),
    ]
    assert [child["identifier"] for child in app["children"][2]["children"]] == ["inner"]


def test_a_hierarchy_past_the_element_limit_is_cut_short_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hierarchy, "ELEMENTS_MAX", 2)
    text = "Application, pid: 1, label: 'App'\n" + "".join(
        f" Button, {{{{0.0, {n}.0}}, {{1.0, 1.0}}}}, label: 'b{n}'\n" for n in range(3)
    )
    document = document_from_hierarchy(text)
    assert document["truncated"] is True and len(document["elements"][0]["children"]) == 1
