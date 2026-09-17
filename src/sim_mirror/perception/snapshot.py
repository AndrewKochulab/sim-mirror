# SPDX-License-Identifier: Apache-2.0
"""What is on a device's screen, as a few lines an agent can read and act on.

A companion's accessibility document for one screen of Settings is 6 KB of JSON; an agent needs a dozen lines of it.
`build` keeps what can be acted on or says where things are -- buttons, fields, switches, cells, text, headings --
drops the containers that only hold them and anything wholly off screen, and writes one line each:

    iOS 26.5 · Settings · 402x874pt · #a91c
    [heading "Settings"]
    e2 button "General" (201,319)
    e12 search ="Search" (201,822)

A **ref** (``e2``) names an element across snapshots. It stays with the element for as long as the element is on
screen -- the same kind, label and identifier, even after it moved -- so an agent can take a snapshot, scroll, and still
tap ``e2`` where ``e2`` now is. A ref is never given to a different element later. Elements that look the same -- the
same kind, label and identifier -- cannot be told apart when one of them is added or removed, so all of those are given
fresh refs then: a ref an agent kept for one of them is refused as not on screen rather than landing on its neighbour.

The point after it is where a tap lands: the middle of the part of the element that is on screen. The **digest**
(``#a91c``) changes whenever what the screen says changes, and `diff` says how -- unless most of the screen changed, a
scroll say, when the whole screen reads better than a list of everything that moved.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, replace

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, ScreenTree

LABEL_MAX = 60

#: Elements that only hold others. Their children are kept; they are not.
CONTAINERS = frozenset(
    {"Application", "Group", "Window", "Other", "ScrollView", "Table", "CollectionView", "Keyboard", "Toolbar",
     "NavigationBar", "TabBar"}
)  # fmt: skip
#: How an element's role reads in a line.
KINDS = {
    "Button": "button", "TextField": "field", "SecureTextField": "secure", "SearchField": "search",
    "Switch": "switch", "Toggle": "switch", "Slider": "slider", "Cell": "cell", "Link": "link", "Tab": "tab",
    "StaticText": "text", "Image": "image", "Heading": "heading", "Stepper": "stepper", "Picker": "picker",
    "SegmentedControl": "segments", "Key": "key",
}  # fmt: skip
#: Kinds that say where things are rather than being acted on: shown, but given no ref.
CONTEXT = frozenset({"heading"})
#: Kinds worth a line even with nothing written on them: an empty field is still a field.
ENTRY = frozenset({"field", "secure", "search"})
#: A diff at least this share of the whole screen's length is replaced by the whole screen.
DIFF_SHARE = 0.75
#: Said when nothing on the screen could be read. The usual cause, measured: after UI tests the device's apps stop
#: answering accessibility (kAXErrorServerNotFound) -- through a reinstall, a relaunch and minutes of waiting -- while
#: the home screen still reads; a restart of the device brings them back.
NOTHING_READ = (
    "nothing on this screen could be read -- the app may still be loading, or the device's apps have stopped "
    "answering accessibility, as they can after UI tests: sim_device restart brings them back; sim_screenshot still "
    "shows the screen"
)
#: Said instead when the screen's pixels were read too, and had no text either.
NOTHING_SEEN = (
    "nothing on this screen could be read, not even text in its pixels -- the app may still be loading, or show only "
    "pictures; sim_screenshot still shows the screen"
)

Key = tuple[str, str, str, int]


def _clean(value: str) -> str:
    text = " ".join(value.split())
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


@dataclass(frozen=True)
class Element:
    ref: str | None
    kind: str
    label: str
    value: str | None
    x: float
    y: float
    flags: tuple[str, ...] = ()

    def line(self, *, with_ref: bool = True) -> str:
        if self.ref is None:
            return f'[{self.kind} "{self.label}"]'
        parts = [
            self.ref if with_ref else "",
            self.kind,
            f'"{self.label}"' if self.label else "",
            f'="{self.value}"' if self.value is not None else "",
            f"({round(self.x)},{round(self.y)})",
            *self.flags,
        ]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class Snapshot:
    header: str
    elements: tuple[Element, ...]
    digest: str
    #: ref -> what the ref stands for, so the next snapshot can give it back.
    keys: dict[str, Key]
    next_ref: int
    notes: tuple[str, ...] = ()

    def text(self) -> str:
        return "\n".join([self.header, *(element.line() for element in self.elements), *self.notes])

    def element(self, ref: str) -> Element | None:
        return next((element for element in self.elements if element.ref == ref), None)

    def find(self, text: str) -> Element | None:
        """The first element whose label or value contains `text`, ignoring case."""
        wanted = text.casefold()
        return next(
            (
                element
                for element in self.elements
                if wanted in element.label.casefold() or wanted in (element.value or "").casefold()
            ),
            None,
        )


def _kind(node: ElementNode) -> str:
    kind = KINDS.get(node.role, (node.role or "element").lower())
    if kind == "field" and ("SearchField" in node.traits or node.subrole == "AXSearchField"):
        return "search"
    return kind


def _visible_middle(frame: Frame | None, screen: Screen) -> tuple[float, float] | None:
    """The middle of the part of a frame that is on screen, or None when none of it is."""
    if frame is None:
        return None
    left, top = max(frame.x, 0.0), max(frame.y, 0.0)
    right = min(frame.x + frame.width, float(screen.width_pt))
    bottom = min(frame.y + frame.height, float(screen.height_pt))
    if right <= left or bottom <= top:
        return None
    return (left + right) / 2, (top + bottom) / 2


def _flags(node: ElementNode) -> tuple[str, ...]:
    found = [("selected", "Selected" in node.traits), ("editing", "IsEditing" in node.traits),
             ("disabled", node.disabled)]  # fmt: skip
    return tuple(name for name, on in found if on)


@dataclass(frozen=True)
class LineParts:
    """What an element's line would say, before it is given a ref."""

    kind: str
    label: str
    value: str | None
    middle: tuple[float, float]


def line_parts(node: ElementNode, screen: Screen) -> LineParts | None:
    """What a line for this element would say, or None when it is worth none: a container, wholly off screen, or an
    element that says nothing."""
    if node.role in CONTAINERS:
        return None
    middle = _visible_middle(node.frame, screen)
    if middle is None:
        return None
    kind = _kind(node)
    label = _clean(node.label or node.title)
    value = _clean(node.value) if node.value.strip() else None
    value = None if value == label else value
    if not label and value is None and kind not in ENTRY:
        return None
    return LineParts(kind, label, value, middle)


def says_anything(tree: ScreenTree, screen: Screen) -> bool:
    """Whether a snapshot of this tree would have a line: whether its reader read anything on the screen."""
    return any(line_parts(node, screen) is not None for node in tree.walk())


def from_pixels(listed: int) -> str:
    """Said when lines were read from the screen's pixels."""
    lines = "line was" if listed == 1 else "lines were"
    return f"{listed} {lines} read from the screen's pixels: text may be misread, and a ref taps its middle"


def build(
    tree: ScreenTree, *, device: str, screen: Screen, max_elements: int, previous: Snapshot | None = None
) -> Snapshot:
    """A snapshot of one screen's tree, keeping the refs `previous` gave."""
    app = next((_clean(node.label) for node in tree.walk() if node.role == "Application"), "")
    given = {key: ref for ref, key in previous.keys.items()} if previous else {}
    next_ref = previous.next_ref if previous else 1
    occurrences: dict[tuple[str, str, str], int] = {}
    elements: list[Element] = []
    keys: dict[str, Key] = {}
    #: Context lines already listed: a navigation bar's title and the large title below it are one heading.
    said: set[tuple[str, str]] = set()
    worth_a_line = 0
    pixel_lines = 0
    for node in tree.walk():
        parts = line_parts(node, screen)
        if parts is None:
            continue
        kind, label, value, middle = parts.kind, parts.label, parts.value, parts.middle
        if kind in CONTEXT:
            if (kind, label) in said:
                continue
            said.add((kind, label))
        worth_a_line += 1
        if len(elements) >= max_elements:
            continue
        ref = None
        if kind not in CONTEXT:
            identity = (kind, label, node.identifier)
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            key: Key = (*identity, occurrence)
            ref = given.get(key)
            if ref is None:
                ref, next_ref = f"e{next_ref}", next_ref + 1
            keys[ref] = key
        elements.append(Element(ref, kind, label, value, middle[0], middle[1], _flags(node)))
        if node.source == PIXELS:
            pixel_lines += 1
    if previous is not None:
        # Look-alikes whose number changed: which is which is unknown, so none keeps a ref it could be wrong about.
        before = Counter(key[:3] for key in previous.keys.values())
        unsure = {
            identity for identity, count in occurrences.items() if identity in before and before[identity] != count
        }
        if unsure:
            fresh: list[Element] = []
            for element in elements:
                known = keys.get(element.ref) if element.ref else None
                if (
                    known is not None
                    and element.ref is not None
                    and known[:3] in unsure
                    and given.get(known) == element.ref
                ):
                    ref, next_ref = f"e{next_ref}", next_ref + 1
                    keys[ref] = keys.pop(element.ref)
                    element = replace(element, ref=ref)
                fresh.append(element)
            elements = fresh
    digest = hashlib.blake2b("\n".join(e.line(with_ref=False) for e in elements).encode(), digest_size=8).hexdigest()
    notes: list[str] = []
    if worth_a_line > len(elements):
        notes.append(f"… {worth_a_line - len(elements)} more not shown")
    if tree.modal is not None:
        name = _clean(tree.modal.name)
        notes.append(f"a modal is in front{': ' + name if name else ''}")
    if tree.truncated:
        notes.append("the companion cut this tree short")
    notes.extend(tree.notes)
    if pixel_lines:
        notes.append(from_pixels(pixel_lines))
    if not worth_a_line:
        notes.append(NOTHING_SEEN if tree.pixels else NOTHING_READ)
    header = " · ".join(
        part for part in (device, app, f"{screen.width_pt}x{screen.height_pt}pt", f"#{digest[:4]}") if part
    )
    return Snapshot(
        header=header, elements=tuple(elements), digest=digest, keys=keys, next_ref=next_ref, notes=tuple(notes)
    )


def diff(previous: Snapshot, current: Snapshot) -> str:
    """What changed between two snapshots -- or the whole new one, when most of the screen changed."""
    if previous.digest == current.digest:
        return f"no change · #{current.digest[:4]}"
    before = {element.ref: element for element in previous.elements if element.ref}
    after = {element.ref: element for element in current.elements if element.ref}
    context_before = [element.line() for element in previous.elements if not element.ref]
    context_after = [element.line() for element in current.elements if not element.ref]
    lines = [f"#{previous.digest[:4]} → #{current.digest[:4]}"]
    lines += [f"+ {line}" for line in context_after if line not in context_before]
    lines += [f"- {line}" for line in context_before if line not in context_after]
    lines += [f"+ {element.line()}" for ref, element in after.items() if ref not in before]
    lines += [
        f"~ {element.line()}"
        for ref, element in after.items()
        if ref in before and element.line() != before[ref].line()
    ]
    lines += [f"- {element.line()}" for ref, element in before.items() if ref not in after]
    lines += list(current.notes)
    changes, whole = "\n".join(lines), current.text()
    return whole if len(changes) >= DIFF_SHARE * len(whole) else changes
