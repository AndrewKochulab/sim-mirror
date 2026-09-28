# SPDX-License-Identifier: Apache-2.0
"""What SimMirror changes about how a device looks and where it believes it is, and putting it back.

A person switching a phone to dark, an agent enlarging its text or giving it a demo status bar, a test moving it to
another city: each change goes through the device's `DeviceChanges`. When the device's changes are to be put back
(``device.restore_changes``: a real device's, by default), it first reads how the device was before its first change
of that kind and remembers how to return to it; `restore` does so, newest first, when the device is let go -- so a
person's own phone is left as they had it. When they are not, a change is only made, and nothing is read first. A
setting the device's tool cannot read is changed but not put back, and a change that cannot be undone is logged while
the rest are still undone.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sim_mirror.core.control import DeviceControl, DisplayState
from sim_mirror.platform.errors import DeviceControlError

logger = logging.getLogger(__name__)

Undo = Callable[[], Awaitable[None]]


class DeviceChanges:
    """One device's changes and how to undo each; the first change of each kind is the one remembered."""

    def __init__(self) -> None:
        self._undo: dict[str, Undo] = {}
        self._before: DisplayState | None = None

    @property
    def changed(self) -> list[str]:
        """What has been changed and not yet put back, oldest first."""
        return list(self._undo)

    def on(self, control: DeviceControl, udid: str, *, remember: bool) -> Changes:
        """Changes made through `control`, remembered to be put back when `remember` says."""
        return Changes(self, control, udid, remember)

    async def before(self, control: DeviceControl, udid: str) -> DisplayState:
        """How the device looked before SimMirror first changed it, read once."""
        if self._before is None:
            self._before = await control.display(udid)
        return self._before

    def remember(self, what: str, undo: Undo) -> None:
        self._undo.setdefault(what, undo)

    async def restore(self) -> list[str]:
        """Put back everything changed, newest first. Answers what could not be put back."""
        undo, self._undo, self._before = self._undo, {}, None
        failed: list[str] = []
        for what, step in reversed(list(undo.items())):
            try:
                await step()
            except DeviceControlError as exc:
                logger.warning("could not put back the device's %s: %s", what, exc)
                failed.append(what)
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

    def _keep(self, what: str, was: object, undo: Callable[[], Awaitable[None]]) -> None:
        if self.remember and was is not None:
            self.ledger.remember(what, undo)

    async def appearance(self, mode: str) -> None:
        was = await self._was(lambda state: state.appearance)
        await self.control.appearance(self.udid, mode)
        self._keep("appearance", was, lambda: self.control.appearance(self.udid, str(was)))

    async def text_size(self, size: str) -> None:
        was = await self._was(lambda state: state.text_size)
        await self.control.text_size(self.udid, size)
        self._keep("text_size", was, lambda: self.control.text_size(self.udid, str(was)))

    async def contrast(self, on: bool) -> None:
        was = await self._was(lambda state: state.contrast)
        await self.control.contrast(self.udid, on)
        self._keep("contrast", was, lambda: self.control.contrast(self.udid, bool(was)))

    async def reduce_motion(self, on: bool) -> None:
        was = await self._was(lambda state: state.reduce_motion)
        await self.control.reduce_motion(self.udid, on)
        self._keep("reduce_motion", was, lambda: self.control.reduce_motion(self.udid, bool(was)))

    async def status_bar(self, demo: bool) -> None:
        """The demo status bar, or the device's own again."""
        if not demo:
            await self.control.clear_status_bar(self.udid)
            return
        await self.control.demo_status_bar(self.udid)
        self._keep("status_bar", True, lambda: self.control.clear_status_bar(self.udid))

    async def locate(self, latitude: float, longitude: float) -> None:
        await self.control.locate(self.udid, latitude, longitude)
        self._keep("location", True, lambda: self.control.clear_location(self.udid))

    async def route(self, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        await self.control.route(self.udid, waypoints, speed)
        self._keep("location", True, lambda: self.control.clear_location(self.udid))

    async def clear_location(self) -> None:
        await self.control.clear_location(self.udid)
