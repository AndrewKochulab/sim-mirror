# SPDX-License-Identifier: Apache-2.0
"""What SimMirror changes about how a device looks and where it believes it is, and putting it back.

A person switching a phone to dark, an agent enlarging its text or giving it a demo status bar, a test moving it to
another city: each change goes through the device's `DeviceChanges`. When the device's changes are to be put back
(``device.restore_changes``: a real device's, by default), it first reads how the device was before its first change
of that kind and remembers it; `restore` returns the device to it, newest first, when the device is let go -- so a
person's own phone is left as they had it. When they are not, a change is only made, and nothing is read first. A
setting the device's tool cannot read is changed but not put back, and a change that cannot be undone is logged while
the rest are still undone. A tool that can read each setting exactly (`ExactRestore`, an Android device's) is asked for
what each change replaces, and puts back exactly that -- turning the device included.

What is remembered is written down as well (`ChangeJournal`), so a daemon that ended without letting its devices go --
a crash, a kill -- puts them back the next time it starts; what cannot be put back then, with the device unplugged,
say, is kept for the start after.

Changing a real device's appearance can send the app in front to the background, leaving the home screen showing with
the app still running. Where the device can say which app is in front (WebDriverAgent's ``front_app``), that app is
read before the change and watched for a moment after it (`FRONT_READS` times, `FRONT_READ_S` apart); one sent to the
background is launched again -- not ended first, so it comes back as it was -- and the change's answer says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.core.control import DeviceControl, DisplayState, ExactRestore
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
#: How a value its tool read exactly is written down: ``{"exact": <what it read>}``.
EXACT = "exact"
#: The home screen's own bundle id: what is in front when no app is.
SPRINGBOARD = "com.apple.springboard"
#: How many times, and how far apart, the app in front is read after an appearance change: iOS takes a moment to send
#: an app to the background, and about a second and a half is all a change is held up by looking.
FRONT_READS = 3
FRONT_READ_S = 0.5

#: Which app is in front of a device, by its bundle id; None when that cannot be said now.
FrontApp = Callable[[], Awaitable[str | None]]


async def _put_back(control: DeviceControl, udid: str, what: str, before: Any) -> None:
    """Put back one change: exactly, as the device's tool read it, where it did; else as `UNDO` says."""
    if isinstance(before, dict) and EXACT in before and isinstance(control, ExactRestore):
        await control.put_back(udid, what, before[EXACT])
        return
    step = UNDO.get(what)
    if step is not None:
        await step(control, udid, before)


async def undo(control: DeviceControl, udid: str, was: Mapping[str, Any]) -> list[str]:
    """Put a device back as it was before each change, newest first. Answers what could not be put back."""
    failed: list[str] = []
    for what, before in reversed(list(was.items())):
        try:
            await _put_back(control, udid, what, before)
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
        on_appearance: Callable[[str], None] | None = None,
        front_app: FrontApp | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> Changes:
        """Changes made through `control`, remembered to be put back when `remember` says -- and written down in
        `journal`, when there is one. `on_appearance` is told each appearance the device is given; `front_app` says
        which app is in front, where it can, for an appearance change to bring back one it sent to the background --
        read again `sleep` apart.

        The first time a ledger meets its journal it takes up what the journal still holds for the device -- what an
        earlier session changed and could not put back, the device unplugged -- so that is put back when this one is
        let go, and a change made again does not read the earlier change as how the device was."""
        if journal is not None:
            if remember and not self._udid and (left := journal.pending().get(udid)) is not None:
                self._was = {**left.changes, **self._was}
            self._journal, self._udid, self._developer_dir = journal, udid, developer_dir
        return Changes(self, control, udid, remember, on_appearance or _ignore, front_app, sleep)

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


def _ignore(mode: str) -> None:
    return None


@dataclass(frozen=True)
class Changes:
    """A device's changes, made through one control."""

    ledger: DeviceChanges
    control: DeviceControl
    udid: str
    remember: bool
    #: Told each appearance the device is given, once it has it.
    on_appearance: Callable[[str], None] = _ignore
    #: Which app is in front, where the device can say; None where it cannot.
    front_app: FrontApp | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def _was(self, what: str, read: Callable[[DisplayState], object]) -> object:
        """What a change replaces, to be put back: read exactly, where the device's tool can."""
        if not self.remember:
            return None
        if isinstance(self.control, ExactRestore):
            return {EXACT: await self.control.saved(self.udid, what)}
        return read(await self.ledger.before(self.control, self.udid))

    def _keep(self, what: str, was: object) -> None:
        if self.remember and was is not None:
            self.ledger.remember(what, was)

    async def appearance(self, mode: str) -> str | None:
        """Light or dark. Answers what else became of the device -- the app in front sent to the background, and
        brought back or not -- or None."""
        read = self.front_app
        front = await read() if read is not None else None
        was = await self._was("appearance", lambda state: state.appearance)
        await self.control.appearance(self.udid, mode)
        self._keep("appearance", was)
        self.on_appearance(mode)
        if read is None or front is None or front == SPRINGBOARD:
            return None
        return await self._bring_back(front, read)

    async def _bring_back(self, front: str, read: FrontApp) -> str | None:
        """Bring `front` -- the app in front before a change -- back to the front when the change sent it to the
        background: launched again, not ended first. Answers what was done, or None when it stayed in front."""
        for _ in range(FRONT_READS):
            await self.sleep(FRONT_READ_S)
            now = await read()
            if now is not None and now != front:
                break
        else:
            return None
        try:
            await self.control.launch(self.udid, front)
        except DeviceControlError as exc:
            logger.warning("could not bring %s back to the front of %s: %s", front, self.udid, exc)
            return f"{front} went to the background with the change and could not be brought back: {exc}"
        return f"{front} went to the background with the change, and was brought back to the front"

    async def text_size(self, size: str) -> None:
        was = await self._was("text_size", lambda state: state.text_size)
        await self.control.text_size(self.udid, size)
        self._keep("text_size", was)

    async def contrast(self, on: bool) -> None:
        was = await self._was("contrast", lambda state: state.contrast)
        await self.control.contrast(self.udid, on)
        self._keep("contrast", was)

    async def reduce_motion(self, on: bool) -> None:
        was = await self._was("reduce_motion", lambda state: state.reduce_motion)
        await self.control.reduce_motion(self.udid, on)
        self._keep("reduce_motion", was)

    async def status_bar(self, demo: bool) -> None:
        """The demo status bar, or the device's own again."""
        if not demo:
            await self.control.clear_status_bar(self.udid)
            return
        was: object = True
        if self.remember and isinstance(self.control, ExactRestore) and "status_bar" not in self.ledger.changed:
            was = {EXACT: await self.control.saved(self.udid, "status_bar")}
        await self.control.demo_status_bar(self.udid)
        self._keep("status_bar", was)

    async def locate(self, latitude: float, longitude: float) -> None:
        await self.control.locate(self.udid, latitude, longitude)
        self._keep("location", True)

    async def route(self, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        await self.control.route(self.udid, waypoints, speed)
        self._keep("location", True)

    async def clear_location(self) -> None:
        await self.control.clear_location(self.udid)

    async def turning(self) -> None:
        """The device is about to be turned: how it lies now is remembered, where its tool can put that back."""
        if self.remember and isinstance(self.control, ExactRestore) and "rotation" not in self.ledger.changed:
            self._keep("rotation", {EXACT: await self.control.saved(self.udid, "rotation")})
