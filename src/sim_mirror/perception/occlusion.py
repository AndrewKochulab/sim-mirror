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
Health its tab bar but no navigation bar, and Settings its toolbar or the search field on it, one or the other from
one read to the next. A search field docked at the foot of the screen, where iOS 26 puts one in a toolbar, says where
that toolbar is (`_docked`). Otherwise what it does not list covers nothing here; what is listed near the top, under
a navigation bar no reader lists, is hit-tested before a tap (`perception.in_front`).

The status bar is in front too: a tap in the strip at the top of the screen lands on it, and scrolls a list to its
top. Where it ends is where a navigation bar a reader lists begins -- the screen's top safe area, 62 points on an
iPhone 17 Pro and less on others -- so a row scrolled up past that navigation bar is not tapped in the strip, and
with nothing of it clear below the strip says ``covered by status bar`` (measured in Files on iOS 26.5, 2026-10-16).
What is in the status bar is not under it. With no navigation bar listed, where the strip ends is not known here;
what is listed near the top is then hit-tested before it is tapped (`perception.in_front`).

A bar whose frame takes up most of the screen is not drawn there: iOS 26 gives a toolbar the whole screen's frame and
draws it as a capsule at the bottom. Such a bar covers only where what is inside it is -- or, where a bare group lies
across the foot of the screen around that, the whole group (`foot_group`): on iOS 27 the toolbar a search field floats
on takes taps from all of it, 60 points above the capsule in Settings, where a tap on a row listed there opened nothing
(an iPhone 17 Pro, 27.0, 2026-10-27). A docked search field no bar is listed around says the same (`_docked`).

An element covers nothing inside it -- a tab bar's own tabs are on it, not under it -- nor anything it is inside, and
nothing covers the keyboard, which is in front of everything. What a reader merged in found (the app SDK, Xcode's
hierarchy, the screen's pixels) it covers too, unless it sits wholly on the cover: idb_companion lists a tab bar with
none of its tabs, and the tabs the app SDK finds are on the bar, while a row it finds half under the bar is under it.
A modal is not a cover: in front of one, readers list only what is in it.

What is drawn in front of a bar without being on it -- a floating button over a tab bar -- is taken here for what is
under it, when the bar's reader found it: accessibility lists what a person reads in the order they read it, not the
order it is drawn, and its tree says nothing of which is in front. Its hit test does, and a touch on such an element
asks it first (`perception.in_front`). None of the stock apps measured on iOS 26.5 has one (Health's and Photos'
search button is part of the tab bar, and accessibility lists neither).
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
#: How far, in points, a bar's frame may stop short of the screen's edge, or what is on a cover reach past its frame.
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
#: How a search field is known, by role, trait or subrole: idb lists one as a text field with the trait.
SEARCH_ROLE = "SearchField"
SEARCH_SUBROLE = "AXSearchField"
#: How far above the foot of the screen a search field's foot may be, in points, for it to be docked there, in iOS 26's
#: toolbar: Settings' is 33 points above, and one above the keyboard hundreds.
DOCKED_PT = 44.0
#: How far above a docked search field the toolbar around it begins, where no group in the tree says: 15 points in
#: Settings (measured, iOS 26.5).
DOCKED_MARGIN_PT = 15.0
#: The tallest a bare group across the foot of the screen may be and still be the toolbar a docked search field is on.
#: iOS 27 lists that toolbar as such a group, reaching far above the field: 126 points tall from 60 above it in Settings
#: on an iPhone 17 Pro (27.0, 2026-10-27), where a tap on the row it covers -- General, its middle 26 points above the
#: field -- landed on the toolbar and opened nothing.
FOOT_GROUP_MAX_PT = 180.0
#: What the status bar is, as a line names it.
STATUS_BAR = "status bar"
#: The trait iOS gives what is in the status bar, and the role readers that list the bar give it.
STATUS_BAR_TRAIT = "StatusBarElement"
STATUS_BAR_ROLE = "StatusBar"
#: The lowest a navigation bar's top may be, in points, for what is above it to be the status bar: the strip is the
#: screen's top safe area, 62 points on an iPhone 17 Pro and 20 to 68 on others, and a navigation bar further down is
#: a popover's or a sheet's.
STATUS_BAR_MAX_PT = 80.0


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


def _within(inner: Frame, outer: Frame) -> bool:
    """Whether one frame is wholly inside another, give or take `EDGE_PT`."""
    return (
        inner.x >= outer.x - EDGE_PT
        and inner.y >= outer.y - EDGE_PT
        and inner.x + inner.width <= outer.x + outer.width + EDGE_PT
        and inner.y + inner.height <= outer.y + outer.height + EDGE_PT
    )


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


def _where(node: ElementNode, kind: str, screen: Screen, tree: ScreenTree | None = None) -> Frame | None:
    """Where an element covers, on screen: its frame, or for a bar framed as most of the screen, where what is inside
    it is -- or, where a bare group across the foot of the screen holds that, the group (`_foot_around`)."""
    frame = _clip(node.frame, screen)
    most = BAR_SHARE * screen.width_pt * screen.height_pt
    if frame is None or kind == KEYBOARD or _area(frame) <= most:
        return frame
    inside = (_clip(each.frame, screen) for each in node.walk() if each is not node)
    drawn = _bounds(part for part in inside if part is not None and _area(part) <= most)
    around = _foot_around(tree, drawn, screen) if tree is not None and drawn is not None else None
    return around or drawn


def _foot_around(tree: ScreenTree, drawn: Frame, screen: Screen) -> Frame | None:
    """The tallest bare group across the foot of the screen that holds what a bar draws: on iOS 27 the toolbar a search
    field floats on takes taps from the whole of it, 60 points above the capsule in Settings (`FOOT_GROUP_MAX_PT`)."""
    held = [
        node.frame
        for node in tree.walk()
        if node.role in GROUPS and not node.label and node.frame is not None
        if foot_group(node.frame, screen.width_pt, screen.height_pt) and node.frame.y <= drawn.y
    ]
    return min(held, key=lambda frame: frame.y, default=None)


def covers(tree: ScreenTree, screen: Screen) -> tuple[Cover, ...]:
    """Everything in this tree that covers what is under it."""
    found: list[Cover] = []
    keys: list[tuple[ElementNode, tuple[int, ...]]] = []

    def visit(node: ElementNode, around: tuple[int, ...]) -> None:
        kind = _kind(node, screen)
        if kind is not None:
            frame = _where(node, kind, screen, tree)
            if frame is not None:
                exempt = frozenset((*around, *(id(each) for each in node.walk())))
                found.append(Cover(kind, frame, exempt, node.source))
        elif node.role == KEY_ROLE or KEY_TRAIT in node.traits:
            keys.append((node, around))
        for child in node.children:
            visit(child, (*around, id(node)))

    for root in tree.roots:
        visit(root, ())
    found.extend(_docked(tree, found, screen))
    if keys and not any(cover.kind == KEYBOARD for cover in found):
        placed = (_clip(key.frame, screen) for key, _around in keys)
        keyboard = _bounds(frame for frame in placed if frame is not None)
        if keyboard is not None:
            exempt = frozenset(each for key, around in keys for each in (*around, *(id(n) for n in key.walk())))
            found.append(Cover(KEYBOARD, keyboard, exempt, keys[0][0].source))
    strip = _status_bar(tree, found, screen)
    if strip is not None:
        found.append(strip)
    return tuple(found)


def _docked(tree: ScreenTree, found: Sequence[Cover], screen: Screen) -> list[Cover]:
    """The toolbar around a search field docked at the foot of the screen, where no reader listed it: Settings lists
    its toolbar or the search field on it, from one read to the next, and the field's capsule is all that says where
    the toolbar is. It covers from just above the field to the foot of the screen; what is on the field -- its
    dictation button -- is the toolbar's own."""
    docked: list[Cover] = []
    for node in tree.walk():
        frame = node.frame
        searching = node.role == SEARCH_ROLE or SEARCH_ROLE in node.traits or node.subrole == SEARCH_SUBROLE
        if (
            not searching
            or frame is None
            or screen.height_pt - (frame.y + frame.height) > DOCKED_PT
            or frame.y + frame.height > screen.height_pt + EDGE_PT
            or frame.width < screen.width_pt / 2
            or any(cover.kind != KEYBOARD and _shared(cover.frame, frame) is not None for cover in found)
        ):
            continue
        top = _toolbar_top(tree, frame, screen)
        exempt = frozenset(id(each) for each in tree.walk() if each.frame is not None and _within(each.frame, frame))
        bar = Frame(0.0, top, float(screen.width_pt), screen.height_pt - top)
        docked.append(Cover(OCCLUDER_KINDS["Toolbar"], bar, exempt | {id(node)}, node.source))
    return docked


def _toolbar_top(tree: ScreenTree, field: Frame, screen: Screen) -> float:
    """Where the toolbar a docked search field is on begins: at the top of the tallest bare group across the foot of
    the screen that reaches above the field, where the tree lists one (`foot_group`) -- iOS 27's -- else just above
    the field."""
    tops = [
        node.frame.y
        for node in tree.walk()
        if node.role in GROUPS and not node.label and node.frame is not None
        if foot_group(node.frame, screen.width_pt, screen.height_pt) and node.frame.y <= field.y
    ]
    return min(tops, default=max(field.y - DOCKED_MARGIN_PT, 0.0))


def foot_group(frame: Frame, width: float, height: float) -> bool:
    """Whether a frame lies as a bare group across the foot of a screen of this size does when it is the toolbar a
    docked search field is on: the whole width, down to the foot, and at most `FOOT_GROUP_MAX_PT` tall."""
    return (
        frame.x <= EDGE_PT
        and frame.x + frame.width >= width - EDGE_PT
        and abs(frame.y + frame.height - height) <= EDGE_PT
        and 0 < frame.height <= FOOT_GROUP_MAX_PT
    )


def _status_bar(tree: ScreenTree, found: Sequence[Cover], screen: Screen) -> Cover | None:
    """The status bar, where a navigation bar's top says it ends: a tap there lands on it, and scrolls a list to its
    top. None when no navigation bar says: what is listed in the top strip is then hit-tested before a tap
    (`perception.in_front`)."""
    navigation = OCCLUDER_KINDS["NavigationBar"]
    tops = [cover for cover in found if cover.kind == navigation and EDGE_PT < cover.frame.y <= STATUS_BAR_MAX_PT]
    if not tops:
        return None
    below = min(tops, key=lambda cover: cover.frame.y)
    shown = (node for node in tree.walk() if node.role == STATUS_BAR_ROLE or STATUS_BAR_TRAIT in node.traits)
    exempt = below.exempt | frozenset(id(each) for node in shown for each in node.walk())
    return Cover(STATUS_BAR, Frame(0.0, 0.0, float(screen.width_pt), below.frame.y), exempt, below.source)


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
        if id(node) not in cover.exempt
        and _shared(visible, cover.frame) is not None
        and (cover.source == node.source or not _within(visible, cover.frame))
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
