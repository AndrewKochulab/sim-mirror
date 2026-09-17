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
from sim_mirror.perception.model import Modal
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import build

EXAMPLES = Path(__file__).resolve().parents[4] / "protocol" / "app-sdk" / "v1" / "examples"
BUNDLE = "io.github.andrewkochulab.simmirror.appsdk"
PID = 4242
SCREEN = Screen(1206, 2622, 402, 874, 3.0)


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return loaded


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
    document = read(example("hierarchy-uikit.json"))
    assert lines(document) == [
        '[heading "Form"]',
        'e1 button "trash" (368,89)',
        'e2 field "Title" ="Groceries" (201,162)',
        'e3 secure "Password" (201,218)',
        'e4 switch "Notifications" ="1" (201,272)',
        'e5 segments "list.bullet, square.grid.2x2" ="list.bullet" (201,321)',
        'e6 button "Rating control" ="3" (110,373)',
        'e7 button "Weekly promo" (201,470)',
        'e8 text "Weekly promo" (136,437)',
        'e9 button "Save" (201,585) disabled',
    ]
    assert document.app == SharedApp(name="AppSDK", bundle_id=BUNDLE, sdk_version="1.0.0")
    assert document.screen == SCREEN and document.protocol == 1 and document.truncated is False
    assert document.elements == 10 and document.notes == ()


def test_a_swiftui_screen_reads_with_its_tags_and_traits() -> None:
    document = read(example("hierarchy-swiftui.json"))
    assert lines(document) == [
        'e1 text "Welcome back" (130,137)',
        'e2 button "gearshape" (368,89)',
        'e3 button "Daily mix" (201,260)',
        'e4 image "sunset" (201,240)',
        'e5 text "Daily mix" (96,319)',
        'e6 switch "Toggle" ="0" (201,376)',
        'e7 button "Pay now" (201,725) selected',
    ]
    tree = tree_from_document(document.document, source=wire.SOURCE)
    assert {node.identifier for node in tree.walk()} >= {"checkout.pay"}
    assert {node.source for node in tree.walk()} == {"app"}


def test_what_the_keyboard_covers_is_left_out_and_the_field_being_edited_says_so() -> None:
    document = read(example("hierarchy-keyboard.json"))
    assert lines(document) == ['e1 search "Search" ="harb" (201,162) editing', 'e2 cell "sunset" (201,230)']


def test_a_modal_is_named_and_what_the_app_says_it_left_out_is_kept() -> None:
    document = read(example("hierarchy-alert.json"))
    tree = tree_from_document(document.document)
    assert tree.modal == Modal("Delete photo?") and tree.truncated is False
    assert document.truncated is True
    assert document.notes == ("swiftui debug data unavailable: unexpected shape",)
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
