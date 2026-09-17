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

`FallbackReader` reads a second reader only when the first read nothing -- the screen's pixels, when accessibility
says nothing -- and `compose` puts a device's readers together as its scope's ``perception.ocr`` asks: the trees
merged, then the pixels as a fallback, merged in too, or not at all. `CombinedExtraReaders` is the one slot several
kinds of extra reader share.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Literal, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Screen, ScreenReader
from sim_mirror.perception.model import ElementNode, Frame, Modal, ScreenTree
from sim_mirror.perception.snapshot import CONTAINERS, says_anything

logger = logging.getLogger(__name__)

IDB = "idb"
_PUNCTUATION = re.compile(r"[^\w\s]")
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


class CombinedExtraReaders:
    """Several kinds of extra readers in the one slot: each one's readers, in the order given."""

    def __init__(self, *parts: ExtraReaders) -> None:
        self._parts = parts

    def readers(self, udid: str, connector: str, config: SimConfig) -> Sequence[TreeReader]:
        return tuple(reader for part in self._parts for reader in part.readers(udid, connector, config))

    def forget(self, udid: str) -> None:
        for part in self._parts:
            part.forget(udid)

    async def close(self) -> None:
        """Let go of every part's readers; one that fails to close does not keep the rest open."""
        for part in self._parts:
            try:
                await part.close()
            except Exception:
                logger.exception("closing screen readers failed")


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
    """Text as the words it says: case, spacing and punctuation aside -- one reader writes ``What’s`` and another,
    reading pixels, ``What's``."""
    return " ".join(_PUNCTUATION.sub(" ", text.casefold()).split())


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
        pixels = tree.pixels
        for other in self._others:
            try:
                found = await other.read()
            except self._failures as exc:
                logger.debug("a screen reader could not read the screen: %s", exc)
                notes.append(str(exc))
                continue
            modal = modal or found.modal
            pixels = pixels or found.pixels
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
        if not added and modal == tree.modal and pixels == tree.pixels and len(notes) == len(tree.notes):
            return tree
        return replace(tree, roots=(*tree.roots, *added), modal=modal, notes=tuple(notes), pixels=pixels)


#: Said when accessibility read nothing on a screen and its pixels were read instead.
ACCESSIBILITY_SAID_NOTHING = (
    "accessibility said nothing on this screen, so its pixels were read -- if the app has controls, its accessibility "
    "may have stopped answering, as it can after UI tests: sim_device restart brings it back"
)


class FallbackReader:
    """The first reader's tree when it says anything; otherwise the fallback's, with what the first one said.

    A first reader that fails falls back too, and why is said. A fallback that fails leaves the first reader's tree
    with why; both failing is the first one's failure."""

    def __init__(
        self,
        primary: TreeReader,
        fallback: TreeReader,
        *,
        screen: Screen,
        failures: tuple[type[BaseException], ...] = (ConnectorError,),
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._screen = screen
        self._failures = failures

    async def read(self) -> ScreenTree:
        try:
            tree = await self._primary.read()
        except self._failures as exc:
            try:
                found = await self._fallback.read()
            except self._failures:
                raise exc from None
            return replace(found, notes=(f"accessibility could not be read: {exc}", *found.notes))
        if says_anything(tree, self._screen):
            return tree
        try:
            found = await self._fallback.read()
        except self._failures as exc:
            logger.debug("the screen's pixels could not be read: %s", exc)
            return replace(tree, notes=(*tree.notes, str(exc)))
        return replace(
            found, modal=tree.modal or found.modal, notes=(*tree.notes, ACCESSIBILITY_SAID_NOTHING, *found.notes)
        )


def compose(
    *,
    structured: Sequence[TreeReader],
    pixels: TreeReader | None,
    mode: Literal["off", "fallback", "merge"],
    screen: Screen,
) -> TreeReader | None:
    """How a device's screen is read: its trees -- the connector's first, then any merged in -- and its pixels, as a
    scope's ``perception.ocr`` asks. None when nothing can read it."""
    trees = MergedReader(structured[0], structured[1:]) if structured else None
    if pixels is None or mode == "off":
        return trees
    if trees is None:
        return pixels
    if mode == "merge":
        return MergedReader(structured[0], (*structured[1:], pixels))
    return FallbackReader(trees, pixels, screen=screen)
