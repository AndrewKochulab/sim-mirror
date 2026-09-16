# SPDX-License-Identifier: Apache-2.0
"""Where a screen's tree comes from.

A `TreeReader` answers the screen as a `ScreenTree`. A connector's `ScreenReader` answers an accessibility document
-- idb_companion's, or Xcode 27's UI hierarchy through ``mcpbridge`` read into the same shape -- and `DocumentReader`
reads it, marking each element with the reader that found it.

`MergedReader` puts readers together: the first reader's tree is kept whole, and a later one only adds what the first
did not already say. Readers describe the same element differently -- idb's ``CheckBox`` is Xcode's ``Switch``, and
Xcode writes the text inside a button again as text -- so an element counts as already said when an earlier one in
the same place says it, whatever its role. A later reader that cannot read the screen does not stop the snapshot:
why is kept as a note, which the snapshot then says.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, ScreenReader
from sim_mirror.perception.model import ElementNode, Frame, Modal, ScreenTree
from sim_mirror.perception.snapshot import CONTAINERS

logger = logging.getLogger(__name__)

IDB = "idb"
#: How far, in points, an element may reach past the one it is inside and still count as inside it.
SLACK_PT = 2.0


class TreeReader(Protocol):
    async def read(self) -> ScreenTree:
        """What is on screen now. Raises when the screen cannot be read."""
        ...


class ExtraReaders(Protocol):
    """The readers a device's snapshots merge in besides its connector's own, chosen by the scope's settings."""

    def readers(self, udid: str, connector: str, config: SimConfig) -> Sequence[TreeReader]:
        """What else to read this device's screen with now; empty for nothing else."""
        ...

    def forget(self, udid: str) -> None:
        """The device ended: let go of whatever was kept for it."""
        ...

    async def close(self) -> None:
        """Let go of everything."""
        ...


class NoExtraReaders:
    """Only the connector's own reader."""

    def readers(self, udid: str, connector: str, config: SimConfig) -> Sequence[TreeReader]:
        return ()

    def forget(self, udid: str) -> None:
        return None

    async def close(self) -> None:
        return None


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


class DocumentReader:
    """The screen as a connector's `ScreenReader` reads it, each element marked with the reader's `name`."""

    def __init__(self, source: ScreenReader, name: str = IDB) -> None:
        self._source = source
        self.name = name

    async def read(self) -> ScreenTree:
        return tree_from_document(await self._source.accessibility(), source=self.name)


def _identity(node: ElementNode) -> tuple[str, str, str, tuple[int, ...] | None]:
    frame = node.frame
    place = None if frame is None else (round(frame.x), round(frame.y), round(frame.width), round(frame.height))
    return node.role, node.label, node.identifier, place


def _words(text: str) -> str:
    return " ".join(text.casefold().split())


def _inside(inner: Frame | None, outer: Frame | None) -> bool:
    """Whether the middle of one frame is within another."""
    if inner is None or outer is None:
        return False
    x, y = inner.x + inner.width / 2, inner.y + inner.height / 2
    return (
        outer.x - SLACK_PT <= x <= outer.x + outer.width + SLACK_PT
        and outer.y - SLACK_PT <= y <= outer.y + outer.height + SLACK_PT
    )


def _said_by(node: ElementNode, earlier: ElementNode) -> bool:
    """Whether an earlier element in the same place already says what this one does."""
    if not _inside(node.frame, earlier.frame):
        return False
    if node.identifier and node.identifier == earlier.identifier:
        return True
    return bool(node.label) and _words(node.label) in _words(earlier.label)


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
        speakers = [node for node in tree.walk() if node.role not in CONTAINERS]
        added: list[ElementNode] = []
        notes = list(tree.notes)
        modal = tree.modal
        for other in self._others:
            try:
                found = await other.read()
            except self._failures as exc:
                logger.debug("a screen reader could not read the screen: %s", exc)
                notes.append(str(exc))
                continue
            modal = modal or found.modal
            notes.extend(found.notes)
            for node in found.walk():
                identity = _identity(node)
                if identity in known or node.role in CONTAINERS or not (node.label or node.value or node.identifier):
                    continue
                if any(_said_by(node, speaker) for speaker in speakers):
                    continue
                known.add(identity)
                speakers.append(node)
                added.append(replace(node, children=()))
        if not added and modal == tree.modal and len(notes) == len(tree.notes):
            return tree
        return replace(tree, roots=(*tree.roots, *added), modal=modal, notes=tuple(notes))
