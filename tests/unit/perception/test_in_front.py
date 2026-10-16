# SPDX-License-Identifier: Apache-2.0
"""Which elements a tap asks the device about before it lands, and what its answers make of the element."""

from __future__ import annotations

from typing import Any

from sim_mirror.perception.in_front import NAVIGATION_BAND_PT, asked, judged
from sim_mirror.perception.model import Frame
from sim_mirror.perception.readers import node_from, tree_from_document
from sim_mirror.perception.snapshot import Element, build
from sim_mirror.testing.fakes import SCREEN, fixture_json, point_key


def element(y: float, height: float = 52, covered: str | None = None, frame: bool = True) -> Element:
    framed = Frame(16, y, 370, height) if frame else None
    middle = max(y, 0) + (min(y + height, SCREEN.height_pt) - max(y, 0)) / 2
    return Element("e1", "button", "Row", None, 201, middle, covered=covered, frame=framed)


def test_only_what_a_snapshot_cannot_tell_is_asked_about() -> None:
    assert asked(element(820, covered="tab bar"), frozenset({"tab bar"}), SCREEN) == ((201, 846),)
    for unasked in ("keyboard", "status bar"):
        assert asked(element(820, covered=unasked), frozenset({unasked}), SCREEN) == (), unasked
    assert asked(element(60, frame=False), frozenset(), SCREEN) == (), "no frame to weigh an answer against"
    assert asked(element(60), frozenset({"navigation bar"}), SCREEN) == (), "the listed bar says"
    assert asked(element(NAVIGATION_BAND_PT), frozenset(), SCREEN) == (), "below the band"
    assert asked(element(60), frozenset({"tab bar"}), SCREEN) == ((201, 86),)
    assert asked(element(40, height=140), frozenset(), SCREEN) == ((201, 110), (201, 148)), "and below the band"
    assert asked(element(80, height=50), frozenset(), SCREEN) == ((201, 105),), "too little below the band to ask"


def test_the_recorded_answers_say_what_is_in_front() -> None:
    # Settings on iOS 26.5 scrolled up under the navigation bar it does not list, and the answers idb_companion gave.
    recorded: dict[str, Any] = fixture_json("ax-settings-under-navigation-bar-ios26.json")
    snapshot = build(tree_from_document(recorded["tree"]), device="", screen=SCREEN, max_elements=50)
    found = {}
    for each in snapshot.elements:
        points = asked(each, snapshot.covers, SCREEN) if each.ref else ()
        if points:
            answers = [recorded["points"].get(point_key(*point)) for point in points]
            nodes = [node_from(answer, "idb") if answer else None for answer in answers]
            found[each.ref] = judged(each, points, nodes).covered
    assert found == {"e1": "status bar", "e2": "navigation bar", "e14": "toolbar"}
