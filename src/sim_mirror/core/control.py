# SPDX-License-Identifier: Apache-2.0
"""What SimMirror asks of a device's own tools, whichever kind of device it is.

A connector owns a device's screen, input and element tree; everything else -- installing and launching apps, opening
a URL, the pasteboard, light and dark, the log -- goes through the device's own tool: simctl for a simulator, devicectl
for a real device. `DeviceControl` is what the tools and the core ask for, so neither knows which it is talking to;
`SimulatorControl` is simctl's side of it. What a device's tool cannot do is not here as a stub: its connector leaves
the capability out, and the tool asking is refused before it calls.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sim_mirror.platform.devicectl import Devicectl, DevicectlError, text_size_name
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


class DeviceLogs(Protocol):
    """A real device's log as SimMirror keeps it while the device is attached."""

    def lines(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str] | None:
        """The device's log lines of the last `since_s` seconds -- an app's, or errors and faults -- or None when
        nothing of this device's is kept."""
        ...


class NoDeviceLogs:
    """Where no real device's log is kept."""

    def lines(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str] | None:
        return None


class PhysicalControl:
    """`DeviceControl` for a real device, through devicectl.

    A real device's status bar cannot be set: it shows a demo one by itself while its screen is mirrored over a
    cable, so taking one away is nothing to do.
    """

    def __init__(self, devicectl: Devicectl, logs: DeviceLogs | None = None) -> None:
        self.devicectl = devicectl
        self._logs = logs or NoDeviceLogs()

    async def install(self, udid: str, app_path: str) -> None:
        await self.devicectl.install(udid, app_path)

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        return await self.devicectl.launch(udid, bundle_id, args, terminate_running=terminate_running)

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        """Quit an app: the process running from its bundle, found by where each process runs from."""
        app = next(
            (app for app in await self.devicectl.apps(udid, bundle_id=bundle_id) if app.bundle_id == bundle_id), None
        )
        if app is None or not app.url:
            return False
        bundle = app.url if app.url.endswith("/") else app.url + "/"
        running = [process for process in await self.devicectl.processes(udid) if process.executable.startswith(bundle)]
        for process in running:
            await self.devicectl.terminate(udid, process.pid)
        return bool(running)

    async def openurl(self, udid: str, url: str) -> None:
        await self.devicectl.open_url(udid, url)

    async def pbcopy(self, udid: str, text: str) -> None:
        await self.devicectl.pbcopy(udid, text)

    async def display(self, udid: str) -> DisplayState:
        said = await self.devicectl.appearance(udid)
        style = said.get("userInterfaceStyle")
        contrast = said.get("increaseContrast")
        motion = said.get("reduceMotion")
        return DisplayState(
            appearance=style if style in APPEARANCES else None,
            text_size=text_size_name(said.get("textSize")),
            contrast=contrast if isinstance(contrast, bool) else None,
            reduce_motion=motion.get("enabled") if isinstance(motion, dict) else None,
        )

    async def appearance(self, udid: str, mode: str) -> None:
        await self.devicectl.set_appearance(udid, mode=mode)

    async def text_size(self, udid: str, size: str) -> None:
        await self.devicectl.set_appearance(udid, text_size=size)

    async def contrast(self, udid: str, on: bool) -> None:
        await self.devicectl.set_appearance(udid, increase_contrast="on" if on else "off")

    async def reduce_motion(self, udid: str, on: bool) -> None:
        await self.devicectl.set_appearance(udid, reduce_motion="on" if on else "off")

    async def demo_status_bar(self, udid: str) -> None:
        raise DevicectlError(
            "A real device's status bar cannot be set; it shows 9:41 by itself while its screen is mirrored over a "
            "cable."
        )

    async def clear_status_bar(self, udid: str) -> None:
        return None

    async def locate(self, udid: str, latitude: float, longitude: float) -> None:
        await self.devicectl.locate(udid, latitude, longitude)

    async def route(self, udid: str, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        route = {
            "mode": "interval",
            "interval": 1.0,
            "speed": speed,
            "waypoints": [{"latitude": latitude, "longitude": longitude} for latitude, longitude in waypoints],
        }
        with tempfile.TemporaryDirectory(prefix="sim-mirror-route-") as folder:
            path = Path(folder) / "route.json"
            path.write_text(json.dumps(route), encoding="utf-8")
            await self.devicectl.route(udid, path)

    async def clear_location(self, udid: str) -> None:
        await self.devicectl.clear_location(udid)

    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        kept = self._logs.lines(udid, since_s=since_s, bundle_id=bundle_id)
        if kept is None:
            raise DevicectlError(
                "A real device's log is read over its cable: plug it in, and SimMirror keeps its log while it drives "
                "the device."
            )
        return kept
