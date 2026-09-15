# SPDX-License-Identifier: Apache-2.0
"""The screen as a tree of elements, whichever reader found them.

An `ElementNode` keeps what a reader says of one element as it said it -- its role (``Button``, ``StaticText``,
``Application``), label, title, identifier, value, frame in points, traits -- and which reader it came from. Deciding
what is worth a line, and how to write it, is `snapshot`'s business.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


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
    #: The reader that found it: ``idb``, or another reader merged in.
    source: str = ""

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

    def walk(self) -> Iterator[ElementNode]:
        for root in self.roots:
            yield from root.walk()
