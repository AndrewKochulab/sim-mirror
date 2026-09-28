# SPDX-License-Identifier: Apache-2.0
"""What SimMirror asks of a device's own tools, whichever kind of device it is.

A connector owns a device's screen, input and element tree; everything else -- installing and launching apps, opening
a URL, the pasteboard, light and dark, the log -- goes through the device's own tool: simctl for a simulator, devicectl
for a real device. `DeviceControl` is what the tools and the core ask for, so neither knows which it is talking to;
`SimulatorControl` is simctl's side of it. What a device's tool cannot do is not here as a stub: its connector leaves
the capability out, and the tool asking is refused before it calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sim_mirror.platform.simctl import Simctl


class AppControl(Protocol):
    """Apps on a device, and URLs opened in them."""

    async def install(self, udid: str, app_path: str) -> None: ...

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        """Launch an app, answering its pid when the tool says it; `terminate_running` ends a running copy first."""
        ...

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        """Quit an app, answering whether one was running."""
        ...

    async def openurl(self, udid: str, url: str) -> None: ...


class LogReader(Protocol):
    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        """The device's log lines of the last `since_s` seconds: an app's, when `bundle_id` names one, else the errors
        and faults. Oldest first, one line each, without a header."""
        ...


class Pasteboard(Protocol):
    async def pbcopy(self, udid: str, text: str) -> None:
        """Put text on the device's pasteboard."""
        ...


class Look(Protocol):
    async def appearance(self, udid: str, mode: str) -> None:
        """Switch the device to ``light`` or ``dark``."""
        ...


class DeviceControl(AppControl, LogReader, Pasteboard, Look, Protocol):
    """Everything SimMirror asks of a device's own tool."""


class SimulatorControl:
    """`DeviceControl` for a simulator, through simctl."""

    def __init__(self, simctl: Simctl) -> None:
        self.simctl = simctl

    async def install(self, udid: str, app_path: str) -> None:
        await self.simctl.install(udid, app_path)

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        return await self.simctl.launch(udid, bundle_id, args, terminate_running=terminate_running)

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        return await self.simctl.terminate(udid, bundle_id)

    async def openurl(self, udid: str, url: str) -> None:
        await self.simctl.openurl(udid, url)

    async def pbcopy(self, udid: str, text: str) -> None:
        await self.simctl.pbcopy(udid, text)

    async def appearance(self, udid: str, mode: str) -> None:
        await self.simctl.appearance(udid, mode)

    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        if bundle_id is not None:
            predicate = f'subsystem == "{bundle_id}" OR process == "{bundle_id.rsplit(".", 1)[-1]}"'
        else:
            predicate = "messageType == error OR messageType == fault"
        shown = await self.simctl.log_show(udid, since_s=since_s, predicate=predicate)
        # simctl's first line is the column header.
        return [row for row in shown.splitlines()[1:] if row.strip()]
