# SPDX-License-Identifier: Apache-2.0
"""A cabled real device's live screen: read over its cable by SimMirror's native helper, the way QuickTime reads it.

The helper's capture mode (``sim-mirror-helper capture``) serves the screen as the native connector's helper serves a
simulator's -- screenshots in milliseconds and H.264 at up to 60 frames a second -- so the session's screen is the same
`HelperClient`. It needs a helper that says it can capture, and macOS's Camera permission, which the helper asks for
itself the first time.

A device's screen is found among the Mac's capture devices by its name. When another cabled device has the same name,
a screenshot taken through devicectl goes with the helper to tell them apart; the capture device found is remembered,
so that is done once.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, ScreenSource
from sim_mirror.connectors.native.helper import (
    AskVersion,
    CachedVersions,
    CaptureTarget,
    HelperLauncher,
    helper_able,
    helper_version,
)
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display, PhysicalDevice

#: What a helper that can read a cabled device's screen says it can do.
FEATURE = "capture"
#: How much longer than a first picture the helper's hello may take: listing the Mac's capture devices, and asking
#: for the Camera the first time.
HELLO_SLACK_S = 10.0


@dataclass(frozen=True)
class LiveScreen:
    """A device's screen as its cable shows it, while the helper reading it runs."""

    screen: ScreenSource
    alive: Callable[[], bool]
    close: Callable[[], Awaitable[None]]


class LiveScreens(Protocol):
    async def open(
        self, device: PhysicalDevice, display: Display, config: SimConfig, devicectl: Devicectl, *, twins: bool
    ) -> LiveScreen:
        """The device's live screen. Raises `ConnectorUnavailable` saying why it cannot be had."""
        ...

    async def reap_orphans(self) -> int:
        """End every helper a previous run of this host left reading a screen; how many there were."""
        ...


class CableCapture:
    """Opens cabled devices' live screens through the native helper."""

    def __init__(
        self,
        launcher: HelperLauncher,
        *,
        candidates: Callable[[], Sequence[Path]],
        copy: HostCopy | None = None,
        ask_version: AskVersion = helper_version,
    ) -> None:
        self._launcher = launcher
        self._candidates = candidates
        self._copy = copy or HostCopy()
        self._versions = CachedVersions(ask_version)
        #: The capture device each device was found to be, by its UDID.
        self._found: dict[str, str] = {}

    async def open(
        self, device: PhysicalDevice, display: Display, config: SimConfig, devicectl: Devicectl, *, twins: bool
    ) -> LiveScreen:
        binary, why = await helper_able(
            FEATURE,
            "show a cabled device's screen",
            config.native_helper_path,
            self._candidates(),
            self._versions,
            self._copy,
        )
        if binary is None:
            raise ConnectorUnavailable(why or "", 409)
        wait = float(config.real_devices_capture_timeout)
        reference = await self._reference(device, devicectl) if twins and device.udid not in self._found else None
        target = CaptureTarget(
            device.udid,
            device.name,
            display.width_px,
            display.height_px,
            display.scale,
            capture_id=self._found.get(device.udid),
            reference=reference,
        )
        try:
            running = await self._launcher.start_capture(
                binary, target, wait_s=wait, idle_key_frames=config.native_idle_key_frames
            )
            try:
                hello = await running.engine.hello(wait + HELLO_SLACK_S)
            except (ConnectorError, asyncio.CancelledError) as exc:
                await self._launcher.stop(running)
                if isinstance(exc, ConnectorError):
                    raise ConnectorUnavailable(str(exc), 409) from exc
                raise
        finally:
            if reference is not None:
                with contextlib.suppress(OSError):
                    os.unlink(reference)
        if hello.source:
            self._found[device.udid] = hello.source

        async def close() -> None:
            await self._launcher.stop(running)

        return LiveScreen(running.engine, lambda: running.alive, close)

    async def _reference(self, device: PhysicalDevice, devicectl: Devicectl) -> Path | None:
        """A screenshot of the device taken through devicectl, beside the helper's socket; None when none can be."""
        path = self._launcher.file_for(device.udid, ".reference.png")
        try:
            await devicectl.screenshot(device.udid, path)
        except DevicectlError:
            return None
        return path if path.exists() else None

    async def reap_orphans(self) -> int:
        return await self._launcher.reap_orphans()
