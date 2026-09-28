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
from dataclasses import dataclass
from typing import Protocol

from sim_mirror.platform.simctl import APPEARANCES, CONTENT_SIZES, Simctl, SimctlError

#: The text sizes a device takes, smallest first.
TEXT_SIZES = CONTENT_SIZES


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


@dataclass(frozen=True)
class DisplayState:
    """How a device looks now; None for what its tool cannot say."""

    appearance: str | None = None
    text_size: str | None = None
    contrast: bool | None = None
    reduce_motion: bool | None = None


class Display(Protocol):
    """How a device looks: light or dark, its text size, and the accessibility settings that change what is drawn."""

    async def display(self, udid: str) -> DisplayState:
        """How the device looks now, so a change can be put back."""
        ...

    async def appearance(self, udid: str, mode: str) -> None:
        """Switch the device to ``light`` or ``dark``."""
        ...

    async def text_size(self, udid: str, size: str) -> None:
        """Set the preferred text size, by content size category (`TEXT_SIZES`)."""
        ...

    async def contrast(self, udid: str, on: bool) -> None: ...

    async def reduce_motion(self, udid: str, on: bool) -> None: ...


class StatusBar(Protocol):
    async def demo_status_bar(self, udid: str) -> None:
        """A status bar for a demo: 9:41, full signal and a full battery."""
        ...

    async def clear_status_bar(self, udid: str) -> None: ...


class Place(Protocol):
    """Where the device believes it is."""

    async def locate(self, udid: str, latitude: float, longitude: float) -> None: ...

    async def route(self, udid: str, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        """Move along waypoints at `speed` metres a second."""
        ...

    async def clear_location(self, udid: str) -> None: ...


class DeviceControl(AppControl, LogReader, Pasteboard, Display, StatusBar, Place, Protocol):
    """Everything SimMirror asks of a device's own tool."""


#: simctl's flags for the demo status bar.
DEMO_STATUS_BAR = (
    "--time",
    "9:41",
    "--dataNetwork",
    "wifi",
    "--wifiMode",
    "active",
    "--wifiBars",
    "3",
    "--cellularMode",
    "active",
    "--cellularBars",
    "4",
    "--batteryState",
    "charged",
    "--batteryLevel",
    "100",
)
#: What simctl prints for a switch.
_SWITCH = {"enabled": True, "disabled": False}


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

    async def display(self, udid: str) -> DisplayState:
        appearance = await self.simctl.ui(udid, "appearance")
        size = await self.simctl.ui(udid, "content_size")
        contrast = await self.simctl.ui(udid, "increase_contrast")
        return DisplayState(
            appearance=appearance if appearance in APPEARANCES else None,
            text_size=size if size in TEXT_SIZES else None,
            contrast=_SWITCH.get(contrast),
        )

    async def text_size(self, udid: str, size: str) -> None:
        await self.simctl.content_size(udid, size)

    async def contrast(self, udid: str, on: bool) -> None:
        await self.simctl.increase_contrast(udid, on)

    async def reduce_motion(self, udid: str, on: bool) -> None:
        raise SimctlError("A simulator's reduce motion cannot be set: simctl has no way to. A real device's can.")

    async def demo_status_bar(self, udid: str) -> None:
        await self.simctl.status_bar(udid, DEMO_STATUS_BAR)

    async def clear_status_bar(self, udid: str) -> None:
        await self.simctl.clear_status_bar(udid)

    async def locate(self, udid: str, latitude: float, longitude: float) -> None:
        await self.simctl.location(udid, latitude, longitude)

    async def route(self, udid: str, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        await self.simctl.route(udid, waypoints, speed)

    async def clear_location(self, udid: str) -> None:
        await self.simctl.clear_location(udid)

    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        if bundle_id is not None:
            predicate = f'subsystem == "{bundle_id}" OR process == "{bundle_id.rsplit(".", 1)[-1]}"'
        else:
            predicate = "messageType == error OR messageType == fault"
        shown = await self.simctl.log_show(udid, since_s=since_s, predicate=predicate)
        # simctl's first line is the column header.
        return [row for row in shown.splitlines()[1:] if row.strip()]
