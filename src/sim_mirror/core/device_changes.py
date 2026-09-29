# SPDX-License-Identifier: Apache-2.0
"""What SimMirror changes about how a device looks and where it believes it is, and putting it back.

A person switching a phone to dark, an agent enlarging its text or giving it a demo status bar, a test moving it to
another city: each change goes through the device's `DeviceChanges`. When the device's changes are to be put back
(``device.restore_changes``: a real device's, by default), it first reads how the device was before its first change
of that kind and remembers it; `restore` returns the device to it, newest first, when the device is let go -- so a
person's own phone is left as they had it. When they are not, a change is only made, and nothing is read first. A
setting the device's tool cannot read is changed but not put back, and a change that cannot be undone is logged while
the rest are still undone.

What is remembered is written down as well (`ChangeJournal`), so a daemon that ended without letting its devices go --
a crash, a kill -- puts them back the next time it starts; what cannot be put back then, with the device unplugged,
say, is kept for the start after.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.core.control import DeviceControl, DisplayState
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.storage.private import ensure_private_dir

logger = logging.getLogger(__name__)

#: How each kind of change is undone, from what the device had before it.
UNDO: Mapping[str, Callable[[DeviceControl, str, Any], Awaitable[None]]] = {
    "appearance": lambda control, udid, was: control.appearance(udid, str(was)),
    "text_size": lambda control, udid, was: control.text_size(udid, str(was)),
    "contrast": lambda control, udid, was: control.contrast(udid, bool(was)),
    "reduce_motion": lambda control, udid, was: control.reduce_motion(udid, bool(was)),
    "status_bar": lambda control, udid, was: control.clear_status_bar(udid),
    "location": lambda control, udid, was: control.clear_location(udid),
}


async def undo(control: DeviceControl, udid: str, was: Mapping[str, Any]) -> list[str]:
    """Put a device back as it was before each change, newest first. Answers what could not be put back."""
    failed: list[str] = []
    for what, before in reversed(list(was.items())):
        step = UNDO.get(what)
        if step is None:
            continue
        try:
            await step(control, udid, before)
        except DeviceControlError as exc:
            logger.warning("could not put back the device's %s: %s", what, exc)
            failed.append(what)
    return failed


@dataclass(frozen=True)
class Left:
    """What a device still has changed, and the Xcode to put it back with."""

    developer_dir: str
    changes: dict[str, Any] = field(default_factory=dict)


class ChangeJournal:
    """What each device still has changed, kept in a private file -- or in memory, where there is none."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._kept: dict[str, Left] = {}

    def pending(self) -> dict[str, Left]:
        """What each device was left with, by its UDID."""
        if self._path is None:
            return dict(self._kept)
        try:
            written = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        found: dict[str, Left] = {}
        for udid, entry in written.items() if isinstance(written, dict) else ():
            if isinstance(entry, dict) and isinstance(entry.get("changes"), dict):
                found[udid] = Left(str(entry.get("developer_dir") or ""), dict(entry["changes"]))
        return found

    def write(self, udid: str, left: Left) -> None:
        """Keep what a device has changed now; nothing left, and it is forgotten."""
        pending = self.pending()
        if left.changes:
            pending[udid] = left
        else:
            pending.pop(udid, None)
        if self._path is None:
            self._kept = pending
            return
        ensure_private_dir(self._path.parent)
        document = {
            udid: {"developer_dir": entry.developer_dir, "changes": entry.changes} for udid, entry in pending.items()
        }
        partial = self._path.with_suffix(".partial")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(document, out, indent=1)
        os.replace(partial, self._path)


class DeviceChanges:
    """One device's changes and what it had before each; the first change of each kind is the one remembered."""

    def __init__(self) -> None:
        self._was: dict[str, Any] = {}
        self._before: DisplayState | None = None
        self._journal = ChangeJournal()
        self._udid = ""
        self._developer_dir = ""

    @property
    def changed(self) -> list[str]:
        """What has been changed and not yet put back, oldest first."""
        return list(self._was)

    def on(
        self,
        control: DeviceControl,
        udid: str,
        *,
        remember: bool,
        journal: ChangeJournal | None = None,
        developer_dir: str = "",
    ) -> Changes:
        """Changes made through `control`, remembered to be put back when `remember` says -- and written down in
        `journal`, when there is one."""
        if journal is not None:
            self._journal, self._udid, self._developer_dir = journal, udid, developer_dir
        return Changes(self, control, udid, remember)

    async def before(self, control: DeviceControl, udid: str) -> DisplayState:
        """How the device looked before SimMirror first changed it, read once."""
        if self._before is None:
            self._before = await control.display(udid)
        return self._before

    def remember(self, what: str, was: Any) -> None:
        if what not in self._was:
            self._was[what] = was
            self._write()

    def _write(self) -> None:
        if self._udid:
            self._journal.write(self._udid, Left(self._developer_dir, dict(self._was)))

    async def restore(self, control: DeviceControl, udid: str) -> list[str]:
        """Put back everything changed, newest first. Answers what could not be put back, which stays written down."""
        was, self._was, self._before = self._was, {}, None
        failed = await undo(control, udid, was)
        self._was = {what: before for what, before in was.items() if what in failed}
        self._write()
        self._was = {}
        return failed


@dataclass(frozen=True)
class Changes:
    """A device's changes, made through one control."""

    ledger: DeviceChanges
    control: DeviceControl
    udid: str
    remember: bool

    async def _was(self, read: Callable[[DisplayState], object]) -> object:
        return read(await self.ledger.before(self.control, self.udid)) if self.remember else None

    def _keep(self, what: str, was: object) -> None:
        if self.remember and was is not None:
            self.ledger.remember(what, was)

    async def appearance(self, mode: str) -> None:
        was = await self._was(lambda state: state.appearance)
        await self.control.appearance(self.udid, mode)
        self._keep("appearance", was)

    async def text_size(self, size: str) -> None:
        was = await self._was(lambda state: state.text_size)
        await self.control.text_size(self.udid, size)
        self._keep("text_size", was)

    async def contrast(self, on: bool) -> None:
        was = await self._was(lambda state: state.contrast)
        await self.control.contrast(self.udid, on)
        self._keep("contrast", was)

    async def reduce_motion(self, on: bool) -> None:
        was = await self._was(lambda state: state.reduce_motion)
        await self.control.reduce_motion(self.udid, on)
        self._keep("reduce_motion", was)

    async def status_bar(self, demo: bool) -> None:
        """The demo status bar, or the device's own again."""
        if not demo:
            await self.control.clear_status_bar(self.udid)
            return
        await self.control.demo_status_bar(self.udid)
        self._keep("status_bar", True)

    async def locate(self, latitude: float, longitude: float) -> None:
        await self.control.locate(self.udid, latitude, longitude)
        self._keep("location", True)

    async def route(self, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        await self.control.route(self.udid, waypoints, speed)
        self._keep("location", True)

    async def clear_location(self) -> None:
        await self.control.clear_location(self.udid)
