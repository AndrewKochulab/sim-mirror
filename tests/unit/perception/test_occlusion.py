# SPDX-License-Identifier: Apache-2.0
"""What covers what: a bar or the keyboard in front of the screen, found however a reader says where it is, and where
a tap on what is under it lands clear of it."""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree
from sim_mirror.perception.occlusion import KEYBOARD, Cover, Placement, covers, place
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import build, diff
from sim_mirror.testing.fakes import SCREEN, fixture, fixture_json

#: The bottom of the screen a tab bar takes on a 402x874 iPhone.
TAB_BAR = Frame(0, 790, 402, 84)


def button(label: str, frame: Frame, **more: Any) -> ElementNode:
    return ElementNode(role="Button", label=label, frame=frame, **more)


def tab_bar(*tabs: ElementNode, role: str = "TabBar", traits: tuple[str, ...] = ()) -> ElementNode:
    return ElementNode(role=role, label="Tab Bar", frame=TAB_BAR, traits=traits, children=tabs)


def lines(*roots: ElementNode) -> list[str]:
    snapshot = build(ScreenTree(roots=roots), device="", screen=SCREEN, max_elements=50)
    return [element.line() for element in snapshot.elements]


def test_a_row_under_a_tab_bar_says_so_and_one_partly_under_it_is_tapped_where_it_shows() -> None:
    # The reported screen: a grid scrolled on under a tab bar, read by a reader that gives the bar its trait.
    under = button("Item 9", Frame(16, 820, 180, 46))
    partly = button("Item 7", Frame(16, 740, 180, 80))
    tabs = [button(name, Frame(x, 795, 94, 54), traits=("Button",)) for name, x in (("Home", 68), ("Grid", 154))]
    bar = ElementNode(role="Group", frame=TAB_BAR, traits=("TabBar",), children=tuple(tabs))
    root = ElementNode(role="Application", label="Grid", children=(partly, under, bar))
    assert lines(root) == [
        'e1 button "Item 7" (106,765)',
        'e2 button "Item 9" (106,843) covered by tab bar',
        'e3 button "Home" (115,822)',
        'e4 button "Grid" (201,822)',
    ]


def test_idb_reads_a_tab_bar_as_a_group_named_tab_bar_and_what_is_under_it_says_so() -> None:
    # Health's Summary on iOS 26.5, read through idb_companion: the tab bar is a group UIKit names "Tab Bar", with no
    # trait to say what it is, and a tap where "Show All Health Data" is switched to the Sharing tab.
    tree = tree_from_document(fixture_json("ax-health-tab-bar-ios26.json"))
    assert [(cover.kind, cover.frame) for cover in covers(tree, SCREEN)] == [("tab bar", Frame(0, 791, 402, 83))]
    said = [element.line() for element in build(tree, device="", screen=SCREEN, max_elements=50).elements]
    assert said[-2:] == [
        'e11 button "Steps, No Data" (201,708)',
        'e12 button "Show All Health Data" (201,820) covered by tab bar',
    ]
    named = ElementNode(role="Button", label="Tab Bar", frame=TAB_BAR)
    assert covers(ScreenTree(roots=(named,)), SCREEN) == (), "only a group is a bar by its name"


def test_a_tab_bar_covers_neither_the_keyboard_in_front_of_it_nor_what_another_reader_found_on_it() -> None:
    typing = tree_from_document(fixture_json("ax-app-sdk-keyboard.json"))
    said = [element.line() for element in build(typing, device="", screen=SCREEN, max_elements=50).elements]
    assert said[-2:] == ['e42 button "Next keyboard" ="English (UK)" (43,840)', 'e43 button "Dictate" (359,840)']
    bar = ElementNode(role="Group", label="Tab Bar", frame=TAB_BAR, source="idb")
    tab = ElementNode(role="Tab", label="Sharing", frame=Frame(110, 795, 94, 54), source="app")
    assert lines(bar, tab) == ['e1 tab "Sharing" (157,822)'], "idb lists no tabs; the app's are on its bar"


def test_a_bar_covers_neither_what_is_on_it_nor_what_it_is_inside() -> None:
    title = button("Back", Frame(8, 70, 60, 40))
    bar = ElementNode(role="NavigationBar", frame=Frame(0, 62, 402, 54), children=(title,))
    holder = button("Sheet", Frame(0, 40, 402, 834), children=(bar,))
    row = button("Row", Frame(0, 60, 402, 50))
    (cover,) = covers(ScreenTree(roots=(holder, row)), SCREEN)
    assert cover.kind == "navigation bar" and cover.frame == Frame(0, 62, 402, 54)
    assert place(title, SCREEN, [cover]) is None and place(holder, SCREEN, [cover]) is None
    assert place(row, SCREEN, [cover]) == Placement((201.0, 85.0), "navigation bar")


def test_a_bar_framed_as_the_whole_screen_covers_only_where_what_is_inside_it_is() -> None:
    # Settings on iOS 26 through Xcode's hierarchy: the toolbar's frame is the screen, its search capsule the bottom.
    tree = tree_from_document(document_from_hierarchy(fixture("mcpbridge-hierarchy-settings.txt")))
    bars = covers(tree, SCREEN)
    assert [(cover.kind, cover.frame) for cover in bars] == [
        ("navigation bar", Frame(0, 62, 402, 106)),
        ("toolbar", Frame(28, 798, 346, 48)),
    ]
    snapshot = build(tree, device="", screen=SCREEN, max_elements=50)
    said = [element.line() for element in snapshot.elements]
    assert 'e12 button "StandBy" (201,822) covered by toolbar' in said, "only slivers of it show beside the capsule"
    assert 'e13 search "Search" (201,822)' in said and 'e1 text "Settings" (82,140)' in said
    empty = ElementNode(role="Toolbar", frame=Frame(0, 0, 402, 874), children=(ElementNode(role="Other"),))
    assert covers(ScreenTree(roots=(empty, ElementNode(role="Toolbar"))), SCREEN) == ()


def test_the_keyboard_is_where_its_keys_are_when_a_reader_lists_no_keyboard() -> None:
    keys = [
        button(letter, Frame(x, 600, 40, 50), traits=("Button", "KeyboardKey")) for letter, x in (("Q", 0), ("P", 362))
    ]
    hidden = ElementNode(role="TextField", label="Note", frame=Frame(16, 610, 370, 30))
    floating = ElementNode(role="Button", traits=("KeyboardKey",))
    (keyboard,) = covers(ScreenTree(roots=(hidden, *keys, floating)), SCREEN)
    assert keyboard.kind == KEYBOARD and keyboard.frame == Frame(0, 600, 402, 50)
    assert lines(hidden, *keys)[0] == 'e1 field "Note" (201,625) covered by keyboard'
    assert place(keys[0], SCREEN, [keyboard]) is None
    known = ElementNode(role="Keyboard", frame=Frame(0, 590, 402, 284), children=tuple(keys))
    assert [cover.frame for cover in covers(ScreenTree(roots=(known,)), SCREEN)] == [Frame(0, 590, 402, 284)]
    assert covers(ScreenTree(roots=(floating,)), SCREEN) == (), "a key with no frame says nowhere"


def test_a_tap_lands_on_what_is_clear_enough_and_a_sliver_is_not() -> None:
    bar = Cover("tab bar", TAB_BAR, frozenset())
    icon = button("Badge", Frame(100, 784, 10, 10))
    assert place(icon, SCREEN, [bar]) == Placement((105.0, 789.0), "tab bar"), "6 of its 10 points show"
    small = button("Dot", Frame(100, 770, 10, 30))
    assert place(small, SCREEN, [bar]) == Placement((105.0, 780.0)), "its 20 clear points are enough"
    top = Cover("navigation bar", Frame(0, 0, 402, 775), frozenset())
    squeezed = button("Row", Frame(0, 770, 100, 30))
    assert place(squeezed, SCREEN, [top, bar]) == Placement((50.0, 785.0), "tab bar"), "the bar it is more under"
    corner = Cover("toolbar", Frame(0, 700, 30, 20), frozenset())
    wide = button("Wide", Frame(0, 700, 100, 100))
    assert place(wide, SCREEN, [corner, bar]) == Placement((50.0, 755.0)), "the largest part clear of both"
    assert place(button("Clear", Frame(0, 100, 40, 40)), SCREEN, [bar]) is None
    assert place(button("Off", Frame(0, 900, 40, 40)), SCREEN, [bar]) is None
    assert covers(ScreenTree(roots=(ElementNode(role="TabBar"),)), SCREEN) == (), "a bar with no frame says nowhere"


def test_a_heading_under_a_bar_is_no_target_and_a_row_going_under_one_reads_as_changed() -> None:
    heading = ElementNode(role="Heading", label="Grid", frame=Frame(16, 800, 200, 40))
    steady = tuple(button(f"Item {n}", Frame(16, 100 + 50 * n, 180, 46)) for n in range(1, 9))
    bar = tab_bar()
    first = build(ScreenTree(roots=(heading, *steady, button("Item 9", Frame(16, 700, 180, 46)), bar)), device="",
                  screen=SCREEN, max_elements=50)  # fmt: skip
    assert first.text().splitlines()[1] == '[heading "Grid"]' and first.text().endswith('e9 button "Item 9" (106,723)')
    moved = ScreenTree(roots=(heading, *steady, button("Item 9", Frame(16, 820, 180, 46)), bar))
    second = build(moved, device="", screen=SCREEN, max_elements=50, previous=first)
    assert diff(first, second).splitlines()[1:] == ['~ e9 button "Item 9" (106,843) covered by tab bar']
