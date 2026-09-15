# SPDX-License-Identifier: Apache-2.0
"""Where a screen's tree comes from.

A `TreeReader` answers the screen as a `ScreenTree`. The one v1 ships reads the idb connector's consolidated
accessibility document (`IdbTreeReader`). Others -- an Xcode 27 ``mcpbridge`` hierarchy, a WebDriverAgent source tree,
an in-app debug hierarchy, OCR -- are merged in by `MergedReader`: the first reader's tree is kept whole, and a later
reader only adds elements the first did not have, each marked with the reader that found it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from sim_mirror.connectors.base import ConnectorError, ScreenReader
from sim_mirror.perception.model import ElementNode, Frame, Modal, ScreenTree

logger = logging.getLogger(__name__)

IDB = "idb"


class TreeReader(Protocol):
    async def read(self) -> ScreenTree:
        """What is on screen now. Raises when the screen cannot be read."""
        ...


def _text(value: Any) -> str:
    """A document's value as text; a missing, empty or false one is no text."""
    return str(value) if value else ""


def _frame(raw: Any) -> Frame | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Frame(
            float(raw.get("x") or 0),
            float(raw.get("y") or 0),
            float(raw.get("width") or 0),
            float(raw.get("height") or 0),
        )
    except (TypeError, ValueError):
        return None


def node_from(raw: Mapping[str, Any], source: str) -> ElementNode:
    """One element of a companion's accessibility document."""
    traits = raw.get("traits")
    return ElementNode(
        role=_text(raw.get("type")),
        label=_text(raw.get("label")),
        title=_text(raw.get("title")),
        identifier=_text(raw.get("identifier")),
        value=_text(raw.get("value")),
        frame=_frame(raw.get("frame")),
        traits=tuple(str(trait) for trait in traits) if isinstance(traits, list) else (),
        subrole=_text(raw.get("subrole")),
        disabled=raw.get("enabled") is False,
        children=nodes_from(raw.get("children"), source),
        source=source,
    )


def nodes_from(raw: Any, source: str) -> tuple[ElementNode, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(node_from(node, source) for node in raw if isinstance(node, dict))


def tree_from_document(document: Mapping[str, Any], *, source: str = IDB) -> ScreenTree:
    """A companion's consolidated accessibility document as a tree."""
    raw_modal = document.get("modal")
    modal = None
    if raw_modal:
        name = _text(raw_modal.get("label") or raw_modal.get("type")) if isinstance(raw_modal, dict) else ""
        modal = Modal(name)
    return ScreenTree(nodes_from(document.get("elements"), source), modal, bool(document.get("truncated")))


class IdbTreeReader:
    """The screen as the idb connector reads it."""

    def __init__(self, source: ScreenReader) -> None:
        self._source = source

    async def read(self) -> ScreenTree:
        return tree_from_document(await self._source.accessibility(), source=IDB)


def _identity(node: ElementNode) -> tuple[str, str, str, tuple[int, ...] | None]:
    frame = node.frame
    place = None if frame is None else (round(frame.x), round(frame.y), round(frame.width), round(frame.height))
    return node.role, node.label, node.identifier, place


class MergedReader:
    """The first reader's tree, with what the others found that it did not."""

    def __init__(
        self,
        primary: TreeReader,
        others: Sequence[TreeReader] = (),
        *,
        failures: tuple[type[BaseException], ...] = (ConnectorError,),
    ) -> None:
        self._primary = primary
        self._others = tuple(others)
        self._failures = failures

    async def read(self) -> ScreenTree:
        tree = await self._primary.read()
        known = {_identity(node) for node in tree.walk()}
        added: list[ElementNode] = []
        for other in self._others:
            try:
                found = await other.read()
            except self._failures as exc:
                logger.debug("a screen reader could not read the screen: %s", exc)
                continue
            for node in found.walk():
                identity = _identity(node)
                if identity in known or not (node.label or node.value or node.identifier):
                    continue
                known.add(identity)
                added.append(replace(node, children=()))
        return replace(tree, roots=(*tree.roots, *added)) if added else tree
