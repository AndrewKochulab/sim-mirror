# SPDX-License-Identifier: Apache-2.0
"""Readers: a companion's document as a tree, read as it said it; and trees merged, the first one kept whole."""

from __future__ import annotations

import asyncio

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, Modal, ScreenTree
from sim_mirror.perception.readers import (
    ACCESSIBILITY_SAID_NOTHING,
    CombinedExtraReaders,
    DocumentReader,
    FallbackReader,
    MergedReader,
    NamingReader,
    NoExtraReaders,
    TreeReader,
    compose,
    name_unlabeled,
    tree_from_document,
)
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
            at("StaticText", "wi fi", 330, 445, 40, 20),
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


def app(role: str, label: str, x: float, y: float, w: float, h: float, **more: object) -> ElementNode:
    return ElementNode(role=role, label=label, frame=Frame(x, y, w, h), source="app", **more)  # type: ignore[arg-type]


def idb(role: str, label: str, x: float, y: float, w: float, h: float, **more: object) -> ElementNode:
    return ElementNode(role=role, label=label, frame=Frame(x, y, w, h), source="idb", **more)  # type: ignore[arg-type]


async def test_a_naming_reader_names_what_the_first_found_unlabeled_and_changes_nothing_else() -> None:
    field = idb("TextField", "", 20, 352, 362, 34, identifier="note")
    slider = idb("Slider", "", 20, 309, 362, 31, value="50%")
    first = ScreenTree(roots=(idb("Application", "Probe", 0, 0, 402, 874, children=(field, slider)),))
    found = ScreenTree(roots=(app("TextField", "Note", 20, 352, 362, 34), app("Slider", "Volume", 21, 309, 360, 31)))
    merged = await MergedReader(Fixed(first), [NamingReader(Fixed(found))]).read()
    named_field, named_slider = merged.roots[0].children
    assert named_field == ElementNode(
        role="TextField", label="Note", identifier="note", frame=field.frame, source="idb"
    )
    assert (named_slider.label, named_slider.value, named_slider.source) == ("Volume", "50%", "idb")
    assert len(merged.roots) == 1, "what named an element is not added again"
    snapshot = build(merged, device="iOS 27.0", screen=Screen(402, 874, 402, 874, 1.0), max_elements=10)
    assert [element.line() for element in snapshot.elements] == [
        'e1 field "Note" (201,369)',
        'e2 slider "Volume" ="50%" (201,324)',
    ]


async def test_a_reader_that_is_not_naming_only_adds() -> None:
    first = ScreenTree(roots=(idb("Button", "", 0, 0, 44, 44),))
    found = ScreenTree(roots=(app("Button", "Trash", 0, 0, 44, 44),))
    merged = await MergedReader(Fixed(first), [Fixed(found)]).read()
    assert [(node.label, node.source) for node in merged.roots] == [("", "idb"), ("Trash", "app")]


def test_naming_prefers_the_same_role_then_the_closest_frame_and_uses_each_label_once() -> None:
    icon = idb("Button", "", 0, 0, 44, 44)
    twin = idb("Button", "", 0, 0, 44, 44)
    first = ScreenTree(roots=(icon, twin))
    found = ScreenTree(
        roots=(
            app("Image", "trash", 0, 0, 44, 44),
            app("Button", "Delete", 2, 2, 42, 42),
            app("Button", "Remove", 0, 0, 44, 44),
        )
    )
    named = name_unlabeled(first, found)
    assert [node.label for node in named.roots] == ["Remove", "Delete"]
    closest = ScreenTree(roots=(app("Image", "big", 0, 0, 44, 50), app("Image", "exact", 0, 0, 44, 44)))
    assert name_unlabeled(ScreenTree(roots=(icon,)), closest).roots[0].label == "exact"


def test_naming_leaves_what_does_not_overlap_enough_says_something_or_holds_others() -> None:
    far = ScreenTree(roots=(app("Button", "Trash", 20, 20, 44, 44),))
    icon = idb("Button", "", 0, 0, 44, 44)
    tree = ScreenTree(roots=(icon,))
    assert name_unlabeled(tree, far) is tree
    assert name_unlabeled(tree, ScreenTree()) is tree
    flat = ScreenTree(roots=(app("Button", "Trash", 0, 0, 44, 0), ElementNode(role="Button", label="Placeless")))
    assert name_unlabeled(tree, flat) is tree
    titled = ScreenTree(roots=(idb("TextField", "", 0, 0, 44, 44, title="Search"),))
    group = ScreenTree(roots=(idb("Group", "", 0, 0, 44, 44), ElementNode(role="Button")))
    found = ScreenTree(roots=(app("Button", "Trash", 0, 0, 44, 44),))
    assert name_unlabeled(titled, found) is titled
    assert name_unlabeled(group, found) is group


def test_a_label_an_element_inside_already_says_names_nothing() -> None:
    cell = idb("Cell", "", 16, 380, 370, 52, children=(idb("StaticText", "General", 30, 392, 100, 28),))
    other = idb("Button", "", 0, 0, 44, 44)
    tree = ScreenTree(roots=(cell, other))
    found = ScreenTree(roots=(app("Cell", "General", 16, 380, 370, 52), app("Button", "Back", 0, 0, 44, 44)))
    named = name_unlabeled(tree, found)
    assert named.roots[0] is cell, "an untouched element is the same element"
    assert named.roots[1].label == "Back"


async def test_the_other_readers_are_read_at_once_and_answer_in_their_order() -> None:
    started = asyncio.Event()

    class Waiting:
        async def read(self) -> ScreenTree:
            await asyncio.wait_for(started.wait(), timeout=5)
            return ScreenTree(notes=("waited",))

    class Starting:
        async def read(self) -> ScreenTree:
            started.set()
            return ScreenTree(notes=("started",))

    first = ScreenTree(roots=(button("Save", 10, source="idb"),))
    merged = await MergedReader(Fixed(first), [Waiting(), Fixed(ConnectorError("no app")), Starting()]).read()
    assert merged.notes == ("waited", "no app", "started")


async def test_when_the_first_reader_fails_the_others_are_stopped() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Failing:
        async def read(self) -> ScreenTree:
            await started.wait()
            raise ConnectorError("no tree")

    class Slow:
        async def read(self) -> ScreenTree:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ScreenTree()  # pragma: no cover - never reached: the reader is cancelled

    with pytest.raises(ConnectorError, match="no tree"):
        await MergedReader(Failing(), [Slow()]).read()
    assert cancelled.is_set()


async def test_a_naming_reader_that_cannot_read_names_nothing_and_says_why() -> None:
    first = ScreenTree(roots=(idb("Button", "", 0, 0, 44, 44),))
    merged = await MergedReader(Fixed(first), [NamingReader(Fixed(ConnectorError("the app went away")))]).read()
    assert merged.roots == first.roots and merged.notes == ("the app went away",)


async def test_merging_says_whether_pixels_were_read_to_make_the_tree() -> None:
    first = ScreenTree(roots=(button("Save", 10, source="idb"),))
    pixels = ScreenTree(roots=(button("Save", 10, source=PIXELS),), pixels=True)
    merged = await MergedReader(Fixed(first), [Fixed(pixels)]).read()
    assert merged.pixels is True and merged.roots == first.roots
    assert (await MergedReader(Fixed(pixels), [Fixed(first)]).read()).pixels is True


class Parts:
    """Extra readers of one kind, noting what they were asked."""

    def __init__(self, *readers: TreeReader, fail_close: bool = False) -> None:
        self._readers = readers
        self.forgotten: list[str] = []
        self.closed = False
        self._fail_close = fail_close

    def readers(self, udid: str, connector: str, config: SimConfig) -> tuple[TreeReader, ...]:
        return self._readers

    def forget(self, udid: str) -> None:
        self.forgotten.append(udid)

    async def close(self) -> None:
        self.closed = True
        if self._fail_close:
            raise RuntimeError("could not close")


async def test_combined_extra_readers_read_every_parts_readers_in_order_and_let_go_of_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    a, b, c = Fixed(ScreenTree()), Fixed(ScreenTree()), Fixed(ScreenTree())
    first, second = Parts(a, b, fail_close=True), Parts(c)
    combined = CombinedExtraReaders(first, second)
    assert combined.readers("U", "idb", SimConfig.defaults()) == (a, b, c)
    combined.forget("U")
    assert first.forgotten == second.forgotten == ["U"]
    with caplog.at_level("ERROR"):
        await combined.close()
    assert first.closed and second.closed and "closing screen readers failed" in caplog.text
    assert CombinedExtraReaders().readers("U", "idb", SimConfig.defaults()) == ()


SCREEN = Screen(1206, 2622, 402, 874, 3.0)
SAID = ScreenTree(roots=(button("Save", 10, source="idb"),), notes=("from idb",))
SILENT = ScreenTree(roots=(ElementNode(role="Application", label="Game"),), modal=Modal("sheet"), notes=("quiet",))
READ = ScreenTree(roots=(button("Play", 300, source=PIXELS),), notes=("from pixels",), pixels=True)


async def test_a_fallback_is_not_read_while_the_first_reader_says_anything() -> None:
    assert await FallbackReader(Fixed(SAID), Fixed(ConnectorError("unused")), screen=SCREEN).read() is SAID


async def test_a_first_reader_that_says_nothing_falls_back_keeping_what_it_did_say() -> None:
    tree = await FallbackReader(Fixed(SILENT), Fixed(READ), screen=SCREEN).read()
    assert tree.roots == READ.roots and tree.pixels is True and tree.modal == Modal("sheet")
    assert tree.notes == ("quiet", ACCESSIBILITY_SAID_NOTHING, "from pixels")


async def test_a_fallback_that_fails_leaves_the_silent_tree_with_why() -> None:
    tree = await FallbackReader(Fixed(SILENT), Fixed(ConnectorError("Vision failed")), screen=SCREEN).read()
    assert tree.roots == SILENT.roots and tree.notes == ("quiet", "Vision failed")


async def test_a_first_reader_that_fails_falls_back_and_says_why_and_both_failing_is_the_first_ones_failure() -> None:
    broken = ConnectorError("kAXErrorServerNotFound")
    tree = await FallbackReader(Fixed(broken), Fixed(READ), screen=SCREEN).read()
    assert tree.roots == READ.roots
    assert tree.notes == ("accessibility could not be read: kAXErrorServerNotFound", "from pixels")
    with pytest.raises(ConnectorError, match="kAXErrorServerNotFound"):
        await FallbackReader(Fixed(broken), Fixed(ConnectorError("Vision failed")), screen=SCREEN).read()


async def test_a_devices_readers_are_composed_as_its_scope_reads_pixels() -> None:
    tree, extra, pixels = Fixed(SILENT), Fixed(SAID), Fixed(READ)
    off = compose(structured=[tree, extra], pixels=pixels, mode="off", screen=SCREEN)
    assert isinstance(off, MergedReader) and len((await off.read()).roots) == 2
    assert compose(structured=[], pixels=pixels, mode="off", screen=SCREEN) is None
    assert compose(structured=[], pixels=None, mode="fallback", screen=SCREEN) is None
    assert compose(structured=[], pixels=pixels, mode="fallback", screen=SCREEN) is pixels
    assert compose(structured=[], pixels=pixels, mode="merge", screen=SCREEN) is pixels
    without = compose(structured=[tree], pixels=None, mode="merge", screen=SCREEN)
    assert isinstance(without, MergedReader) and await without.read() is SILENT
    fallback = compose(structured=[tree], pixels=pixels, mode="fallback", screen=SCREEN)
    assert isinstance(fallback, FallbackReader) and (await fallback.read()).pixels is True
    merge = compose(structured=[Fixed(SAID)], pixels=pixels, mode="merge", screen=SCREEN)
    assert isinstance(merge, MergedReader)
    assert [node.label for node in (await merge.read()).roots] == ["Save", "Play"]
