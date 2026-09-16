# SPDX-License-Identifier: Apache-2.0
"""Readers: a companion's document as a tree, read as it said it; and trees merged, the first one kept whole."""

from __future__ import annotations

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.perception.model import ElementNode, Frame, Modal, ScreenTree
from sim_mirror.perception.readers import DocumentReader, MergedReader, NoExtraReaders, tree_from_document
from sim_mirror.perception.snapshot import build
from sim_mirror.testing.fakes import FakeEngine, fixture, fixture_json


def test_a_document_reads_as_its_elements_depth_first() -> None:
    tree = tree_from_document(
        {
            "elements": [
                {
                    "type": "Application",
                    "label": "Notes",
                    "children": [
                        {"type": "Button", "label": "Add", "identifier": "add", "frame": {"x": 1, "y": 2, "width": 3,
                                                                                         "height": 4}},
                        "not an element",
                        {"type": "TextField", "value": 0, "title": "Title", "traits": ["IsEditing"],
                         "subrole": "AXSearchField", "enabled": False, "frame": "not a frame"},
                    ],
                }
            ],
            "modal": {"type": "Alert"},
            "truncated": 1,
        }
    )  # fmt: skip
    assert [node.role for node in tree.walk()] == ["Application", "Button", "TextField"]
    add, field = tree.roots[0].children
    assert add == ElementNode(role="Button", label="Add", identifier="add", frame=Frame(1, 2, 3, 4), source="idb")
    assert (field.value, field.title, field.traits, field.subrole, field.disabled, field.frame) == (
        "",
        "Title",
        ("IsEditing",),
        "AXSearchField",
        True,
        None,
    )
    assert tree.modal == Modal("Alert") and tree.truncated is True


def test_what_a_document_leaves_out_or_says_oddly_reads_as_nothing() -> None:
    assert tree_from_document({}) == ScreenTree()
    assert tree_from_document({"elements": "nope", "modal": True}).modal == Modal("")
    odd = tree_from_document({"elements": [{"frame": {"x": "left"}, "traits": "Selected"}]}, source="ocr")
    assert odd.roots[0] == ElementNode(source="ocr")


async def test_a_document_reader_reads_a_connectors_document_marked_with_its_name() -> None:
    tree = await DocumentReader(FakeEngine()).read()
    assert tree == tree_from_document(fixture_json("ax-settings-interactable.json"))
    named = await DocumentReader(FakeEngine(), "mcpbridge").read()
    assert {node.source for node in named.walk()} == {"mcpbridge"}


class Fixed:
    def __init__(self, tree: ScreenTree | Exception) -> None:
        self._tree = tree

    async def read(self) -> ScreenTree:
        if isinstance(self._tree, Exception):
            raise self._tree
        return self._tree


def button(label: str, y: float, *, source: str, identifier: str = "") -> ElementNode:
    return ElementNode(role="Button", label=label, identifier=identifier, frame=Frame(0, y, 100, 40), source=source)


async def test_merging_keeps_the_first_tree_and_adds_only_what_it_did_not_have() -> None:
    first = ScreenTree(roots=(button("Save", 10, source="idb"),), modal=Modal("x"))
    second = ScreenTree(
        roots=(
            ElementNode(role="Group", source="ocr", children=(button("Save", 10.2, source="ocr"),
                                                              button("Share", 60, source="ocr"))),
            ElementNode(role="Image", source="ocr"),
        )
    )  # fmt: skip
    merged = await MergedReader(Fixed(first), [Fixed(ConnectorError("no vision")), Fixed(second)]).read()
    assert [(node.label, node.source) for node in merged.roots] == [("Save", "idb"), ("Share", "ocr")]
    assert merged.modal == Modal("x") and merged.roots[1].children == ()
    assert await MergedReader(Fixed(first)).read() is first
    assert await MergedReader(Fixed(first), [Fixed(ScreenTree())]).read() is first


def at(role: str, label: str, x: float, y: float, w: float, h: float, **more: object) -> ElementNode:
    return ElementNode(role=role, label=label, frame=Frame(x, y, w, h), source="xcode", **more)  # type: ignore[arg-type]


async def test_an_element_an_earlier_reader_already_says_in_the_same_place_is_not_added_whatever_its_role() -> None:
    first = ScreenTree(
        roots=(
            ElementNode(role="Button", label="General", frame=Frame(16, 380, 370, 52), source="idb"),
            ElementNode(role="CheckBox", label="Wi-Fi", identifier="wifi", frame=Frame(16, 440, 370, 44), source="idb"),
        )
    )
    second = ScreenTree(
        roots=(
            at("StaticText", "general", 30, 392, 100, 28),
            at("Switch", "", 325, 440, 63, 28, identifier="wifi"),
            at("Other", "Vertical scroll bar, 2 pages", 369, 62, 30, 750),
            at("Link", "Learn more", 80, 316, 83, 21),
            at("StaticText", "Learn more", 80, 316, 83, 21),
            at("StaticText", "General", 30, 700, 100, 28),
        )
    )
    merged = await MergedReader(Fixed(first), [Fixed(second)]).read()
    assert [(node.role, node.label, node.frame.y if node.frame else None) for node in merged.roots[2:]] == [
        ("Link", "Learn more", 316),
        ("StaticText", "General", 700),
    ]
    placeless = ScreenTree(roots=(ElementNode(role="StaticText", label="General", source="xcode"),))
    assert len((await MergedReader(Fixed(first), [Fixed(placeless)]).read()).roots) == 3


async def test_a_modal_or_a_failure_only_a_later_reader_knows_of_reaches_the_snapshot() -> None:
    first = ScreenTree(roots=(button("OK", 10, source="idb"),), notes=("kept",))
    alert = ScreenTree(roots=(button("OK", 10, source="xcode"),), modal=Modal("Delete?"))
    merged = await MergedReader(Fixed(first), [Fixed(ConnectorError("Xcode has not approved it")), Fixed(alert)]).read()
    assert merged.modal == Modal("Delete?") and merged.notes == ("kept", "Xcode has not approved it")
    assert merged.roots == first.roots
    snapshot = build(merged, device="iOS 27.0", screen=Screen(402, 874, 402, 874, 1.0), max_elements=10)
    assert snapshot.notes == ("a modal is in front: Delete?", "kept", "Xcode has not approved it")


async def test_merging_xcodes_hierarchy_into_idbs_adds_what_idb_leaves_out_and_nothing_it_has() -> None:
    screen = Screen(1206, 2622, 402, 874, 3.0)
    added: dict[str, list[str]] = {}
    for name in ("safari", "settings", "home", "probe", "alert", "keyboard"):
        idb = tree_from_document(fixture_json(f"ax-{name}-ios27.json"))
        xcode = tree_from_document(document_from_hierarchy(fixture(f"mcpbridge-hierarchy-{name}.txt")), source="x")
        before = build(idb, device="iOS 27.0", screen=screen, max_elements=200)
        after = build(await MergedReader(Fixed(idb), [Fixed(xcode)]).read(), device="", screen=screen, max_elements=200)
        added[name] = [element.line() for element in after.elements[len(before.elements) :]]
    assert added == {
        "safari": [
            'e6 text "Example Domain" (174,190)',
            'e7 text "This domain is for use in documentation examples without ne…" (200,260)',
            'e8 link "Learn more" (122,326)',
        ],
        "settings": [],
        "home": [
            'e14 text "WEDNESDAY" (265,115)',
            'e15 text "16" (248,139)',
            'e16 text "No Events Today" (287,198)',
            'e17 pageindicator ="Page 1 of 2" (201,721)',
            'e18 text "23:15" (74,33)',
            'e19 image "Wi-Fi" (323,33)',
        ],
        "probe": [],
        "alert": [],
        "keyboard": [],
    }


async def test_without_extra_readers_only_the_connectors_own_is_read() -> None:
    extra = NoExtraReaders()
    assert extra.readers("U", "idb", SimConfig.defaults()) == ()
    extra.forget("U")
    await extra.close()
