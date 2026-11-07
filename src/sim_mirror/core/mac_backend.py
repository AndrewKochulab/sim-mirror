# SPDX-License-Identifier: Apache-2.0
"""A macOS target is one application, never the desktop.

Automatic cleanup stops only apps we launched; an explicit shutdown quits the selected process.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from sim_mirror.config.model import SimConfig
from sim_mirror.core.backends import Placed
from sim_mirror.core.control import DeviceControl, DisplayState, InstalledApp
from sim_mirror.core.devices import DeviceRef, NoDevice
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.macos import MacOS, bundle
from sim_mirror.protocol import DeviceChoice, DeviceKind
from sim_mirror.scope import Scope

if TYPE_CHECKING:
    from sim_mirror.core.instance import DeviceInstance


class MacControl:
    def __init__(self, mac: MacOS) -> None:
        self.mac = mac

    async def _target(self, udid: str, bundle_id: str) -> None:
        if (await self.mac.app(udid)).bundle_id != bundle_id:
            raise DeviceControlError("app-scoped mode cannot act on another app")

    async def install(self, udid: str, app_path: str) -> None:
        app = bundle(app_path)
        await self._target(udid, app.bundle_id)
        current = await self.mac.app(udid)
        if current.pid is not None and current.path != app.path:
            raise DeviceControlError("terminate the selected app before choosing a different built copy")
        self.mac.known[udid] = app

    async def app_info(self, udid: str, bundle_id: str) -> InstalledApp | None:
        await self._target(udid, bundle_id)
        app = await self.mac.app(udid)
        return InstalledApp(app.bundle_id, app.name, app.version, app.path)

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int:
        await self._target(udid, bundle_id)
        if terminate_running:
            await self.mac.terminate(udid)
        return await self.mac.launch(udid, args)

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        await self._target(udid, bundle_id)
        return await self.mac.terminate(udid)

    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        if bundle_id is not None:
            await self._target(udid, bundle_id)
        return await self.mac.logs(udid, since_s)

    async def display(self, udid: str) -> DisplayState:
        return DisplayState()

    async def openurl(self, udid: str, url: str) -> None:
        raise DeviceControlError("URL dispatch can reach another app; use an in-app link")

    async def pbcopy(self, udid: str, text: str) -> None:
        raise DeviceControlError("the macOS pasteboard is shared; use app-scoped text input")

    async def appearance(self, udid: str, mode: str) -> None:
        raise DeviceControlError("live per-app appearance requires SimMirrorKit")

    async def text_size(self, udid: str, size: str) -> None:
        raise DeviceControlError("text size is system-wide on macOS")

    async def contrast(self, udid: str, on: bool) -> None:
        raise DeviceControlError("contrast is system-wide on macOS")

    async def reduce_motion(self, udid: str, on: bool) -> None:
        raise DeviceControlError("reduce motion is system-wide on macOS")

    async def demo_status_bar(self, udid: str) -> None:
        raise DeviceControlError("macOS apps have no device status bar")

    async def clear_status_bar(self, udid: str) -> None:
        raise DeviceControlError("macOS apps have no device status bar")

    async def locate(self, udid: str, latitude: float, longitude: float) -> None:
        raise DeviceControlError("macOS apps have no simulated device location")

    async def route(self, udid: str, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        raise DeviceControlError("macOS apps have no simulated device location")

    async def clear_location(self, udid: str) -> None:
        raise DeviceControlError("macOS apps have no simulated device location")


class MacBackend:
    kind: DeviceKind = "mac"

    def __init__(self, mac: MacOS) -> None:
        self.mac = mac

    def runs_here(self, device: Placed) -> bool:
        return True

    def withheld(self, instance: DeviceInstance, config: SimConfig) -> str | None:
        return None if config.mac_apps else "macOS apps are off here (mac.apps.enabled)"

    def developer_dir(self, config: SimConfig) -> str:
        return config.developer_dir

    def control(self, developer_dir: str) -> DeviceControl:
        return MacControl(self.mac)

    async def choices(self, config: SimConfig, created: Collection[str]) -> list[DeviceChoice]:
        if not config.mac_apps:
            return []
        return [
            DeviceChoice(
                udid=a.id,
                name=a.name,
                runtime="macOS",
                state="Booted" if a.pid else "Shutdown",
                created=False,
                kind="mac",
                connection=None,
                detail=None,
                usable=True,
            )
            for a in await self.mac.apps()
        ]

    async def lookup(self, udid: str, config: SimConfig) -> DeviceChoice | None:
        return next((a for a in await self.choices(config, ()) if a["udid"] == udid), None)

    async def resolve(self, scope: Scope, remembered: str | None, config: SimConfig) -> DeviceRef:
        chosen = await self.lookup(remembered or "", config)
        if chosen is None:
            raise NoDevice("choose a running macOS app first")
        return DeviceRef(chosen["udid"], chosen["name"], "macOS", False, kind="mac")

    async def prepare(self, instance: DeviceInstance, config: SimConfig) -> None:
        app = await self.mac.app(instance.udid)
        instance.booted_by_us = app.pid is None
        self.mac.held[instance.udid] = await self.mac.launch(instance.udid)

    async def release(self, instance: DeviceInstance, *, shutdown: bool) -> None:
        pid = self.mac.held.pop(instance.udid, None)
        self.mac.windows.pop(instance.udid, None)
        if shutdown and pid is not None:
            try:
                app = await self.mac.app(instance.udid)
                if app.pid == pid:
                    await self.mac.terminate(instance.udid)
            except DeviceControlError:
                pass

    async def gone(self, instances: Sequence[DeviceInstance]) -> list[DeviceInstance]:
        try:
            live = {a.id for a in await self.mac.apps() if a.pid is not None}
        except DeviceControlError:
            return []
        return [instance for instance in instances if instance.udid not in live]
