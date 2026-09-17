# SPDX-License-Identifier: Apache-2.0
"""An app's hierarchy read as an accessibility document: what an agent would see of it, and what is left out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.document import AppDocument, SharedApp, document_from_app
from sim_mirror.connectors.app.errors import AppSdkError
from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import Modal, ScreenTree
from sim_mirror.perception.readers import MergedReader, NamingReader, tree_from_document
from sim_mirror.perception.snapshot import build
from sim_mirror.testing.fakes import fixture_json

EXAMPLES = Path(__file__).resolve().parents[4] / "protocol" / "app-sdk" / "v1" / "examples"
BUNDLE = "io.github.andrewkochulab.simmirror.appsdk"
PID = 4242
SCREEN = Screen(1206, 2622, 402, 874, 3.0)


def example(name: str) -> AppDocument:
    """A hierarchy recorded from examples/app-sdk, read as the app that sent it."""
    raw: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return document_from_app(raw, bundle_id=BUNDLE, pid=raw["app"]["pid"], max_nodes=3000)


def read(raw: Any, max_nodes: int = 3000) -> AppDocument:
    return document_from_app(raw, bundle_id=BUNDLE, pid=PID, max_nodes=max_nodes)


def lines(document: AppDocument) -> list[str]:
    tree = tree_from_document(document.document, source=wire.SOURCE)
    snapshot = build(tree, device="", screen=SCREEN, max_elements=100)
    return [element.line() for element in snapshot.elements]


def node(kind: str = "button", label: str | None = "Go", **more: Any) -> dict[str, Any]:
    return {"kind": kind, "label": label, "frame": {"x": 0, "y": 0, "width": 44, "height": 44}, **more}


def hierarchy(*nodes: Any, **more: Any) -> dict[str, Any]:
    return {"app": {"bundle_id": BUNDLE, "pid": PID, "name": "AppSDK"}, "windows": [{"nodes": list(nodes)}], **more}


def test_a_uikit_screen_reads_as_the_lines_an_agent_sees() -> None:
    document = example("hierarchy-uikit.json")
    assert lines(document) == [
        'e1 field "Title" (201,153)',
        'e2 secure "Password" (201,203)',
        'e3 switch ="0" (48,252)',
        'e4 segments "list.bullet, square.grid.2x2" ="list.bullet" (164,253)',
        'e5 stepper ="2" (299,252)',
        'e6 button "trash" (374,252)',
        'e7 slider "Rating" ="3 of 5" (201,302)',
        'e8 button "Weekly promo, Opened 1 times" (201,380)',
        'e9 text "Weekly promo" (86,372)',
        'e10 text "Opened 1 times" (86,390)',
        'e11 button "Form" (201,84)',
        'e12 button "Share" (364,84)',
        'e13 tab "SwiftUI" (115,822)',
        'e14 tab "Tagged" (201,822)',
        'e15 tab "UIKit" (287,822) selected',
    ]
    assert document.app == SharedApp(name="AppSDK", bundle_id=BUNDLE, sdk_version="1.0.0")
    assert document.screen == SCREEN and document.protocol == 1 and document.truncated is False
    assert document.elements == 17 and document.notes == ()


def test_a_swiftui_screen_reads_its_platform_views_and_its_tags() -> None:
    assert lines(example("hierarchy-swiftui.json")) == [
        'e1 switch ="0" (202,386)',
        'e2 field "Title" (201,437)',
        'e3 secure "Password" (201,491)',
        '[heading "SwiftUI"]',
        'e4 tab "SwiftUI" (115,822) selected',
        'e5 tab "Tagged" (201,822)',
        'e6 tab "UIKit" (287,822)',
    ]
    tagged = example("hierarchy-tagged.json")
    assert lines(tagged) == [
        'e1 switch "Notifications" ="0" (202,365)',
        '[heading "Tagged"]',
        'e2 button "Delete list" (228,198)',
        'e3 button "Play daily mix" (201,281)',
        'e4 tab "SwiftUI" (115,822)',
        'e5 tab "Tagged" (201,822) selected',
        'e6 tab "UIKit" (287,822)',
    ]
    tree = tree_from_document(tagged.document, source=wire.SOURCE)
    assert {node.label for node in tree.walk() if "Named" in node.traits} == {
        "Notifications", "Settings", "Delete list", "Play daily mix",
    }  # fmt: skip
    assert {node.source for node in tree.walk()} == {"app"}


def test_a_name_the_developer_gave_is_marked_as_theirs() -> None:
    document = read(
        hierarchy(
            node(label="Settings", label_source="tag"),
            node(label="Save", label_source="title", traits=["selected"]),
            node(label=None, label_source="tag", identifier="unnamed"),
        )
    )
    assert [element["traits"] for element in document.document["elements"]] == [["Named"], ["Selected"], []]


def test_what_the_keyboard_covers_is_left_out_and_the_field_being_edited_says_so() -> None:
    document = example("hierarchy-keyboard.json")
    assert lines(document)[0] == 'e1 field "Title" (201,153) editing'
    assert not [line for line in lines(document) if " tab " in line], "the tab bar is under the keyboard"


def test_a_modal_is_named_and_what_the_app_says_it_left_out_is_kept() -> None:
    document = example("hierarchy-alert.json")
    tree = tree_from_document(document.document)
    assert tree.modal == Modal("Delete the form?") and tree.truncated is False
    assert lines(document) == ['e1 text "Delete the form?" (201,420)', 'e2 button "Delete" (275,474)',
                               'e3 button "Cancel" (127,474)']  # fmt: skip
    cut = read(hierarchy(node(), truncated=True, notes=["SwiftUI recorded no debug data"]))
    assert cut.truncated is True and cut.notes == ("SwiftUI recorded no debug data",)
    unnamed = read(hierarchy(node(), modal={"kind": "hologram", "name": None}))
    assert tree_from_document(unnamed.document).modal == Modal("Modal")


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "hierarchy",
        {"windows": []},
        {"app": "AppSDK"},
        {"app": {"bundle_id": "com.example.other", "pid": PID}},
        {"app": {"bundle_id": BUNDLE, "pid": 1}},
    ],
)
def test_an_answer_that_is_not_the_listing_apps_hierarchy_is_refused(raw: Any) -> None:
    with pytest.raises(AppSdkError):
        read(raw)


def test_what_says_nothing_or_has_no_place_gives_way_to_what_it_holds() -> None:
    inner = node(label="Inner")
    raw = hierarchy(
        node(kind="container", label=None, children=[inner]),
        node(kind="widget", label=None, children=[node(label="Unknown's child")]),
        node(kind="widget", label="Gauge"),
        node(kind="button", label="Flat", frame={"x": 0, "y": 0, "width": 0, "height": 10}, children=[node("text")]),
        node(kind="button", frame="nowhere", children=[node(label="Frameless's child")]),
        node(kind="button", frame={"x": "left", "y": 0, "width": 1, "height": 1}),
        node(kind="scroll", label=None, identifier="feed", children=[]),
        node(kind="container", label=None, interactive=True),
        node(kind="button", label=None),
        "not a node",
    )
    document = read(raw)
    assert [(element["type"], element["label"]) for element in document.document["elements"]] == [
        ("Button", "Inner"),
        ("Button", "Unknown's child"),
        ("", "Gauge"),
        ("StaticText", "Go"),
        ("Button", "Frameless's child"),
        ("ScrollView", ""),
        ("Other", ""),
        ("Button", ""),
    ]


def test_every_field_is_read_leniently() -> None:
    raw = hierarchy(
        node(
            kind="secure",
            label="Password",
            value="hunter2",
            placeholder="Password",
            identifier="password",
            traits=["selected", "sparkling", {"trait": 1}],
            enabled=False,
        ),
        node(kind="field", label=7, value=None, traits="editing", placeholder=None),
        protocol=True,
        sdk_version="1.0.0; rm -rf",
        screen={"width_pt": 402, "height_pt": 0, "scale": 3},
        notes=["x" * 500, 3, *[f"note {n}" for n in range(9)]],
        keyboard={"frame": "somewhere"},
    )
    raw["app"]["name"] = None
    raw["windows"].extend(["not a window", {"nodes": "not nodes"}])
    document = read(raw)
    secure, field = document.document["elements"]
    assert secure == {
        "type": "SecureTextField",
        "label": "Password",
        "title": "Password",
        "identifier": "password",
        "value": "",
        "frame": {"x": 0.0, "y": 0.0, "width": 44.0, "height": 44.0},
        "traits": ["Selected"],
        "enabled": False,
        "children": [],
    }
    assert (field["label"], field["value"], field["title"], field["traits"]) == ("", "", "", [])
    assert document.protocol == 0 and document.screen is None
    assert document.app == SharedApp(name=BUNDLE, bundle_id=BUNDLE, sdk_version="unknown")
    assert document.notes == ("x" * wire.NOTE_MAX, "note 0", "note 1", "note 2", "note 3")
    assert read({"app": {"bundle_id": BUNDLE, "pid": PID}, "windows": "none", "screen": [], "notes": "x"}).elements == 0
    for screen in ({"width_pt": -402, "height_pt": 874, "scale": 3}, {"width_pt": float("nan"), "scale": 3}):
        assert read(hierarchy(screen=screen)).screen is None


def test_a_hierarchy_is_read_within_a_count_and_a_depth() -> None:
    raw = hierarchy(node(label="One", children=[node(label="Two")]), node(label="Three"))
    capped = read(raw, max_nodes=2)
    assert capped.elements == 2 and capped.truncated is True
    whole = read(raw, max_nodes=3)
    assert whole.elements == 3 and whole.truncated is False
    deep: dict[str, Any] = node(label="Bottom")
    for level in range(wire.DEPTH_MAX + 5):
        deep = node(label=f"Level {level}", children=[deep])
    document = read(hierarchy(deep), max_nodes=10_000)
    assert document.elements == wire.DEPTH_MAX and document.truncated is True
    shallow = read(hierarchy(node(children=[])), max_nodes=10)
    assert shallow.truncated is False


class Fixed:
    def __init__(self, tree: ScreenTree) -> None:
        self._tree = tree

    async def read(self) -> ScreenTree:
        return self._tree


async def test_merging_what_the_sample_app_shares_into_idbs_names_and_adds_what_idb_left_out() -> None:
    changed: dict[str, list[str]] = {}
    for name in ("uikit", "swiftui", "tagged", "keyboard", "alert"):
        idb = tree_from_document(fixture_json(f"ax-app-sdk-{name}.json"))
        app = tree_from_document(example(f"hierarchy-{name}.json").document, source=wire.SOURCE)
        before = {element.line() for element in build(idb, device="", screen=SCREEN, max_elements=200).elements}
        merged = await MergedReader(Fixed(idb), [NamingReader(Fixed(app))]).read()
        after = build(merged, device="", screen=SCREEN, max_elements=200)
        changed[name] = [element.line() for element in after.elements if element.line() not in before]
    assert changed == {
        "uikit": [
            'e2 field "Title" (201,153)',
            'e3 field "Password" (201,203)',
            'e10 segments "list.bullet, square.grid.2x2" ="list.bullet" (164,253)',
            'e11 stepper ="2" (299,252)',
            'e12 slider "Rating" ="3 of 5" (201,302)',
            'e13 button "Weekly promo, Opened 1 times" (201,380)',
            'e14 tab "SwiftUI" (115,822)',
            'e15 tab "Tagged" (201,822)',
            'e16 tab "UIKit" (287,822) selected',
        ],
        "swiftui": [
            'e7 field "Title" (201,437)',
            'e8 field "Password" (201,491)',
            'e10 tab "SwiftUI" (115,822) selected',
            'e11 tab "Tagged" (201,822)',
            'e12 tab "UIKit" (287,822)',
        ],
        "tagged": [
            'e1 button "Settings" (175,198)',
            'e2 button "Delete list" (228,198)',
            'e5 checkbox "Notifications" ="0" (201,365)',
            'e6 button "Play daily mix" (201,281)',
            'e7 tab "SwiftUI" (115,822)',
            'e8 tab "Tagged" (201,822) selected',
            'e9 tab "UIKit" (287,822)',
        ],
        "keyboard": [
            'e2 field "Title" (201,153) editing',
            'e3 field "Password" (201,203)',
            'e44 segments "list.bullet, square.grid.2x2" ="list.bullet" (164,253)',
            'e45 stepper ="2" (299,252)',
            'e46 slider "Rating" ="3 of 5" (201,302)',
            'e47 button "Weekly promo, Opened 1 times" (201,380)',
        ],
        "alert": [],
    }
