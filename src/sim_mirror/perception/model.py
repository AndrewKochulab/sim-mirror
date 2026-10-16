# SPDX-License-Identifier: Apache-2.0
"""The screen as a tree of elements, whichever reader found them.

An `ElementNode` keeps what a reader says of one element as it said it -- its role (``Button``, ``StaticText``,
``Application``), label, title, identifier, value, frame in points, traits -- and which reader it came from. Deciding
what is worth a line, and how to write it, is `snapshot`'s business. A `ScreenTree`'s frames are in the points of the
interface its ``orientation`` says (`perception.orientation`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sim_mirror.perception.orientation import PORTRAIT, Orientation

#: The source of an element read from the screen's pixels rather than from a tree.
PIXELS = "ocr"
#: The trait of an element whose label its app's developer gave it for SimMirror: it names what is in its place even
#: where accessibility already says something else.
NAMED = "Named"


@dataclass(frozen=True)
class Frame:
    """Where an element is, in points."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ElementNode:
    role: str = ""
    label: str = ""
    title: str = ""
    identifier: str = ""
    value: str = ""
    frame: Frame | None = None
    traits: tuple[str, ...] = ()
    subrole: str = ""
    disabled: bool = False
    children: tuple[ElementNode, ...] = ()
    #: The reader that found it: ``idb``, another reader merged in, or `PIXELS`.
    source: str = ""
    #: The names of its custom actions -- a row's swipe actions, ``Delete`` -- where its reader says them: idb_companion
    #: does; the native helper, Xcode's hierarchy, the app SDK and WebDriverAgent do not.
    actions: tuple[str, ...] = ()

    def walk(self) -> Iterator[ElementNode]:
        """This element, then its descendants, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Modal:
    """Something in front of the rest of the screen: an alert, a sheet. Its name may be empty."""

    name: str


@dataclass(frozen=True)
class ScreenTree:
    roots: tuple[ElementNode, ...] = ()
    modal: Modal | None = None
    #: Whether the reader cut the tree short.
    truncated: bool = False
    #: What a reader merged in could not do, said to whoever reads the snapshot.
    notes: tuple[str, ...] = ()
    #: Whether the screen's pixels were read to make it.
    pixels: bool = False
    #: Which way the interface its frames are measured in is turned on the device's screen.
    orientation: Orientation = PORTRAIT

    def walk(self) -> Iterator[ElementNode]:
        for root in self.roots:
            yield from root.walk()
