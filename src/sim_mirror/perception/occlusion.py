# SPDX-License-Identifier: Apache-2.0
"""What is drawn in front of the rest of the screen, and so takes a tap meant for what is under it.

A tab bar, a toolbar, a navigation bar and the keyboard sit in front of the content behind them: a list scrolls on under
a tab bar, and a tap where a row is under one lands on the tab bar. Readers list the row all the same, inside the
screen's bounds. `covers` finds what covers what -- one step every reader's tree goes through, whichever connector
read it -- and `place` says where a tap on an element lands clear of all of it, or what covers it when none of it is
clear.

A cover is found where a reader says it is:

- an element whose role is a bar or the keyboard (`OCCLUDERS`): Xcode's hierarchy, WebDriverAgent and the app SDK
  say ``TabBar``, ``Toolbar``, ``NavigationBar`` and ``Keyboard``;
- a group with the name UIKit gives a tab bar or a toolbar, or whose role is described as a navigation bar's, in any
  of the languages iOS speaks (`perception.bar_names`): how accessibility -- idb_companion and the native helper --
  lists one, with no trait to say what it is and none of what is on it (measured on iOS 26.5 in Health, Files,
  Photos, Settings, Shortcuts and Messages, in English, German, French and Japanese, 2026-10-16);
- an element with the ``TabBar`` trait, which iOS gives a tab bar, where a reader reports it;
- a group shaped and placed as a bar is, though no name says it is one: labelled, holding nothing, across the whole
  width of the screen at its foot and 44 to 100 points tall -- a tab bar or a toolbar in a name UIKit has not used
  yet. It is a ``bar``;
- where a tree has keys but no keyboard -- accessibility lists each key, with the ``KeyboardKey`` trait, and not the
  keyboard around them -- the keyboard is where its keys are.

Accessibility does not always list a bar: Settings lists its navigation bar's back button and title but not the bar,
Health its tab bar but no navigation bar, and Settings its toolbar's search field in some languages and the toolbar
in others. What it does not list covers nothing.

A bar whose frame takes up most of the screen is not drawn there: iOS 26 gives a toolbar the whole screen's frame and
draws it as a capsule at the bottom. Such a bar covers only where what is inside it is.

An element covers nothing inside it -- a tab bar's own tabs are on it, not under it -- nor anything it is inside, and
nothing covers the keyboard, which is in front of everything. A cover covers only what the reader that found it found:
idb_companion lists a tab bar with none of its tabs, and a reader merged in that finds them (the app SDK, Xcode's
hierarchy, the screen's pixels) does not say they are on the bar. A modal is not a cover: in front of one, readers list
only what is in it.

What is drawn in front of a bar without being on it -- a floating button over a tab bar -- is taken for what is under
it, when the bar's reader found it: accessibility lists what a person reads in the order they read it, not the order it
is drawn, and says nothing of which is in front. None of the stock apps measured on iOS 26.5 has one (Health's and
Photos' search button is part of the tab bar, and accessibility lists neither). A tap by point still reaches it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.bar_names import NAVIGATION_BAR_ROLES, TAB_BAR_NAMES, TOOLBAR_NAMES
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree

KEYBOARD = "keyboard"
#: The roles that cover what is under them, as a line names each.
OCCLUDER_KINDS = {"TabBar": "tab bar", "Toolbar": "toolbar", "NavigationBar": "navigation bar", "Keyboard": KEYBOARD}
OCCLUDERS = frozenset(OCCLUDER_KINDS)
#: What a bar nothing names is, as a line names it.
BAR = "bar"
#: The trait iOS gives a tab bar.
TAB_BAR_TRAIT = "TabBar"
#: The roles accessibility gives an element that only holds others.
GROUPS = frozenset({"Group", "Other"})
#: How tall a bar nothing names may be, in points: a tab bar and a toolbar with the home indicator's space below them
#: are 83 and 86 on an iPhone 17 Pro; UIKit's tab bar without that space is 49.
BAR_HEIGHT_PT = (44.0, 100.0)
#: How far, in points, a bar's frame may stop short of the screen's edge.
EDGE_PT = 2.0
#: The trait and the role a key of the keyboard has.
KEY_TRAIT = "KeyboardKey"
KEY_ROLE = "Key"
#: A bar whose frame is more than this share of the screen is drawn only where what is inside it is.
BAR_SHARE = 0.5
#: The least of an element, in points across and down, that must be clear of every cover for a tap there to count as
#: on it: a sliver beside a bar is where a tap is a near miss -- iOS 26's search capsule leaves 12 points of the row
#: under it on each side -- and a covered element costs the agent one swipe, where a miss costs it a wrong screen.
CLEAR_MIN_PT = 20.0


@dataclass(frozen=True)
class Cover:
    """Something in front of the rest of the screen: what it is, as a line names it, and where it covers."""

    kind: str
    frame: Frame
    #: The `id` of each element it does not cover: its own, and those it is inside.
    exempt: frozenset[int]
    #: The reader that found it, which covers only what that reader found.
    source: str = ""


@dataclass(frozen=True)
class Placement:
    """Where a tap on an element lands: the middle of the largest part of it that is clear, or -- when none is --
    the middle of what is on screen, and what covers it."""

    middle: tuple[float, float]
    covered_by: str | None = None


def _clip(frame: Frame | None, screen: Screen) -> Frame | None:
    """The part of a frame on screen, or None when none of it is."""
    if frame is None:
        return None
    left, top = max(frame.x, 0.0), max(frame.y, 0.0)
    right = min(frame.x + frame.width, float(screen.width_pt))
    bottom = min(frame.y + frame.height, float(screen.height_pt))
    if right <= left or bottom <= top:
        return None
    return Frame(left, top, right - left, bottom - top)


def _area(frame: Frame) -> float:
    return frame.width * frame.height


def _bounds(frames: Iterable[Frame]) -> Frame | None:
    """The smallest frame holding all of them, or None for none."""
    listed = list(frames)
    if not listed:
        return None
    left, top = min(f.x for f in listed), min(f.y for f in listed)
    right, bottom = max(f.x + f.width for f in listed), max(f.y + f.height for f in listed)
    return Frame(left, top, right - left, bottom - top)


def _shared(one: Frame, other: Frame) -> Frame | None:
    """Where two frames overlap, or None when they only touch or are apart."""
    left, top = max(one.x, other.x), max(one.y, other.y)
    right = min(one.x + one.width, other.x + other.width)
    bottom = min(one.y + one.height, other.y + other.height)
    if right <= left or bottom <= top:
        return None
    return Frame(left, top, right - left, bottom - top)


def _named(node: ElementNode) -> str | None:
    """What a group is by the name UIKit gives a bar, in any language: a tab bar, a toolbar, a navigation bar."""
    if node.role not in GROUPS:
        return None
    if node.label in TAB_BAR_NAMES:
        return OCCLUDER_KINDS["TabBar"]
    if node.label in TOOLBAR_NAMES:
        return OCCLUDER_KINDS["Toolbar"]
    if node.role_description in NAVIGATION_BAR_ROLES:
        return OCCLUDER_KINDS["NavigationBar"]
    return None


def _shaped(node: ElementNode, screen: Screen) -> bool:
    """Whether a group is shaped and placed as a bar is: labelled, holding nothing, across the foot of the screen."""
    frame = node.frame
    if node.role not in GROUPS or not node.label or node.children or frame is None:
        return False
    low, high = BAR_HEIGHT_PT
    return (
        frame.x <= EDGE_PT
        and frame.x + frame.width >= screen.width_pt - EDGE_PT
        and abs(frame.y + frame.height - screen.height_pt) <= EDGE_PT
        and low <= frame.height <= high
    )


def _kind(node: ElementNode, screen: Screen) -> str | None:
    """What an element is, as a cover: a bar, the keyboard, or None when it covers nothing."""
    kind = OCCLUDER_KINDS.get(node.role) or _named(node)
    if kind is None and TAB_BAR_TRAIT in node.traits:
        return OCCLUDER_KINDS["TabBar"]
    if kind is None and _shaped(node, screen):
        return BAR
    return kind


def _where(node: ElementNode, kind: str, screen: Screen) -> Frame | None:
    """Where an element covers, on screen: its frame, or for a bar framed as most of the screen, where what is inside
    it is."""
    frame = _clip(node.frame, screen)
    most = BAR_SHARE * screen.width_pt * screen.height_pt
    if frame is None or kind == KEYBOARD or _area(frame) <= most:
        return frame
    inside = (_clip(each.frame, screen) for each in node.walk() if each is not node)
    return _bounds(part for part in inside if part is not None and _area(part) <= most)


def covers(tree: ScreenTree, screen: Screen) -> tuple[Cover, ...]:
    """Everything in this tree that covers what is under it."""
    found: list[Cover] = []
    keys: list[tuple[ElementNode, tuple[int, ...]]] = []

    def visit(node: ElementNode, around: tuple[int, ...]) -> None:
        kind = _kind(node, screen)
        if kind is not None:
            frame = _where(node, kind, screen)
            if frame is not None:
                exempt = frozenset((*around, *(id(each) for each in node.walk())))
                found.append(Cover(kind, frame, exempt, node.source))
        elif node.role == KEY_ROLE or KEY_TRAIT in node.traits:
            keys.append((node, around))
        for child in node.children:
            visit(child, (*around, id(node)))

    for root in tree.roots:
        visit(root, ())
    if keys and not any(cover.kind == KEYBOARD for cover in found):
        placed = (_clip(key.frame, screen) for key, _around in keys)
        keyboard = _bounds(frame for frame in placed if frame is not None)
        if keyboard is not None:
            exempt = frozenset(each for key, around in keys for each in (*around, *(id(n) for n in key.walk())))
            found.append(Cover(KEYBOARD, keyboard, exempt, keys[0][0].source))
    return tuple(found)


def _minus(piece: Frame, cut: Frame) -> list[Frame]:
    """What is left of a piece once a cut is taken out of it: up to four pieces, none overlapping."""
    if _shared(piece, cut) is None:
        return [piece]
    right, bottom = piece.x + piece.width, piece.y + piece.height
    cut_right, cut_bottom = cut.x + cut.width, cut.y + cut.height
    top, low = max(piece.y, cut.y), min(bottom, cut_bottom)
    parts = [
        Frame(piece.x, piece.y, piece.width, cut.y - piece.y) if cut.y > piece.y else None,
        Frame(piece.x, cut_bottom, piece.width, bottom - cut_bottom) if cut_bottom < bottom else None,
        Frame(piece.x, top, cut.x - piece.x, low - top) if cut.x > piece.x else None,
        Frame(cut_right, top, right - cut_right, low - top) if cut_right < right else None,
    ]
    return [part for part in parts if part is not None]


def _middle(frame: Frame) -> tuple[float, float]:
    return frame.x + frame.width / 2, frame.y + frame.height / 2


def _holds(frame: Frame, point: tuple[float, float]) -> bool:
    return frame.x <= point[0] <= frame.x + frame.width and frame.y <= point[1] <= frame.y + frame.height


def place(node: ElementNode, screen: Screen, found: Sequence[Cover]) -> Placement | None:
    """Where a tap on an element lands clear of every cover, or what covers it when none of it is clear enough to tap.

    None when no cover is over any of it: a tap lands where it would anyway."""
    visible = _clip(node.frame, screen)
    if visible is None or any(cover.kind == KEYBOARD and id(node) in cover.exempt for cover in found):
        return None
    over = [
        cover
        for cover in found
        if cover.source == node.source and id(node) not in cover.exempt and _shared(visible, cover.frame) is not None
    ]
    if not over:
        return None
    pieces = [visible]
    for cover in over:
        pieces = [rest for piece in pieces for rest in _minus(piece, cover.frame)]
    across, down = min(CLEAR_MIN_PT, visible.width), min(CLEAR_MIN_PT, visible.height)
    clear = [piece for piece in pieces if piece.width >= across and piece.height >= down]
    if clear:
        return Placement(_middle(max(clear, key=_area)))
    middle = _middle(visible)

    def covered(cover: Cover) -> tuple[bool, float]:
        shared = _shared(visible, cover.frame)
        assert shared is not None  # every cover in `over` overlaps it
        return _holds(cover.frame, middle), _area(shared)

    return Placement(middle, max(over, key=covered).kind)
