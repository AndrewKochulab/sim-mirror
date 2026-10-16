# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree
from sim_mirror.perception.orientation import Interface
from sim_mirror.perception.readers import DocumentReader, tree_from_document
from sim_mirror.perception.snapshot import build
from sim_mirror.perception.system_alert import as_drawn
from sim_mirror.testing.fakes import SCREEN, FakeEngine, fixture_json

IPAD = Screen(width_px=2064, height_px=2752, width_pt=1032, height_pt=1376, scale=2.0)
#: Recorded on iOS 26.5 with the device turned as Simulator.app turns it, through idb_companion and the native helper.
TURNED = [
    # An iPhone turned left, Maps asking for the location: drawn landscape right, its root the screen's shape.
    ("ax-alert-turned-right-ios26.json", SCREEN),
    # Turned right, an app asking for notifications: landscape left, its root the interface's shape, two buttons in a
    # row.
    ("ax-alert-turned-left-ios26.json", SCREEN),
    # An iPad upside down: the root upright.
    ("ax-alert-ipad-upside-down-ios26.json", IPAD),
    # An iPad turned right: both readers took it for landscape right.
    ("ax-alert-ipad-landscape-left-ios26.json", IPAD),
]


def documents(recording: str) -> list[dict[str, Any]]:
    """What idb_companion and the native helper each read."""
    recorded = fixture_json(recording)
    return [{**recorded["tree"], "orientation": recorded["orientation"]}, recorded["native"]]


def middles(tree: ScreenTree, interface: Interface) -> dict[str, tuple[int, int]]:
    """Where each element's middle is on the device's screen."""
    found = {}
    for node in tree.walk():
        if node.role != "Application" and node.frame is not None:
            frame = node.frame
            x, y = interface.on_device(frame.x + frame.width / 2, frame.y + frame.height / 2)
            found[node.label] = (round(x), round(y))
    return found


@pytest.mark.parametrize(("recording", "screen"), TURNED)
async def test_a_system_alert_over_a_turned_app_is_read_as_it_is_drawn(recording: str, screen: Screen) -> None:
    drawn = fixture_json(recording)["drawn"]
    for document in documents(recording):
        read = tree_from_document(document)
        # Before, its frames were the screen's points, standing, read in whatever way the reader found.
        assert read.orientation != drawn
        tree = await DocumentReader(FakeEngine(document=document), screen=screen).read()
        assert tree.orientation == drawn
        interface = Interface(screen, drawn)
        size = interface.screen
        assert tree.roots[0].frame == Frame(0, 0, size.width_pt, size.height_pt)
        # Every middle is where it was on the screen, so a touch lands where it did.
        assert middles(tree, interface) == middles(read, Interface(screen))
        snapshot = build(tree, device="iOS 26.5", screen=screen, max_elements=20)
        lines = snapshot.text().splitlines()
        words = interface.words()
        assert lines[0].startswith(f"iOS 26.5 · {size.width_pt}x{size.height_pt}pt {words} · #")
        # Read as drawn: the title first, then the buttons, all on the alert, every button across the interface.
        assert len(snapshot.elements) == len(read.roots[0].children)
        assert snapshot.elements[0].kind == "text" and snapshot.elements[-1].kind == "button"
        buttons = [node.frame for node in tree.walk() if node.role == "Button" and node.frame is not None]
        assert all(frame.width > frame.height for frame in buttons)


async def test_a_system_alert_on_an_upright_device_is_read_as_it_came() -> None:
    for document in documents("ax-alert-upright-ios26.json"):
        read = tree_from_document(document)
        assert as_drawn(read, SCREEN) is read and read.orientation == "portrait"


async def test_a_reader_given_no_screen_reads_the_document_as_it_came() -> None:
    document = documents("ax-alert-turned-right-ios26.json")[0]
    assert await DocumentReader(FakeEngine(document=document)).read() == tree_from_document(document)


def alert(*leaves: ElementNode, label: str = " ", roots: int = 1) -> ScreenTree:
    root = ElementNode(role="Application", label=label, frame=Frame(0, 0, 402, 874), children=leaves)
    return ScreenTree(roots=(root,) * roots)


TITLE = ElementNode(role="StaticText", label="Allow?", frame=Frame(329.7, 307, 42.3, 260))
ALLOW = ElementNode(role="Button", label="Allow", frame=Frame(157, 293, 48, 288))
DENY = ElementNode(role="Button", label="Don't Allow", frame=Frame(45, 293, 48, 288))


@pytest.mark.parametrize(
    "tree",
    [
        # An app's own alert -- its root is named -- is in the app's points already.
        alert(TITLE, ALLOW, DENY, label="Maps"),
        alert(TITLE, ALLOW, DENY, roots=2),
        # One element, no button, or more than an alert has: no alert.
        alert(DENY),
        alert(TITLE, TITLE),
        alert(*([TITLE] * 12), ALLOW),
        # No way lays it out: a square button, or the buttons out of reading order whichever way.
        alert(TITLE, ElementNode(role="Button", label="OK", frame=Frame(100, 400, 48, 48))),
        alert(TITLE, DENY, ALLOW),
        # Off the screen whichever way it is read.
        alert(TITLE, ElementNode(role="Button", label="Far", frame=Frame(500, 900, 48, 288))),
        ScreenTree(),
    ],
)
def test_what_is_no_system_alert_or_lies_no_one_way_is_left_as_read(tree: ScreenTree) -> None:
    assert as_drawn(tree, SCREEN) is tree


def test_an_alert_with_containers_and_frameless_elements_is_turned_whole() -> None:
    group = ElementNode(role="Group", frame=Frame(40, 290, 340, 300), children=(TITLE, ALLOW, DENY))
    nameless = ElementNode(role="Group")
    tree = as_drawn(alert(group, nameless), SCREEN)
    assert tree.orientation == "landscape_right"
    [moved, still] = tree.roots[0].children
    assert moved.frame == pytest.approx(Frame(290, 22, 300, 340)) and still.frame is None
    assert [child.label for child in moved.children] == ["Allow?", "Allow", "Don't Allow"]
