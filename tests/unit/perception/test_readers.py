# SPDX-License-Identifier: Apache-2.0
"""Readers: a companion's document as a tree, read as it said it; and trees merged, the first one kept whole."""

from __future__ import annotations

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.perception.model import ElementNode, Frame, Modal, ScreenTree
from sim_mirror.perception.readers import IdbTreeReader, MergedReader, tree_from_document
from sim_mirror.testing.fakes import FakeEngine, fixture_json


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


async def test_the_idb_reader_reads_the_companions_document() -> None:
    tree = await IdbTreeReader(FakeEngine()).read()
    assert tree == tree_from_document(fixture_json("ax-settings-interactable.json"))


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
