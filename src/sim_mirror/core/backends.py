# SPDX-License-Identifier: Apache-2.0
"""Each kind of device, and how SimMirror lists it, finds a scope's, brings it up and puts it away.

A simulator is listed and booted by simctl, and a scope that has none is given one SimMirror makes; a real device is
listed by devicectl, is never booted or shut down, and is used only once it is picked -- by a person, or by an agent
while ``real_devices.agents_choose`` lets it. `DeviceBackend` is that
difference, one per kind, so `DeviceManager` asks the kind of the device in front of it and nothing else: it never
calls simctl or devicectl itself. What is asked of a device once it runs -- installing, launching, the log -- is its
backend's `DeviceControl`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.core.control import DeviceControl, DeviceLogs, NoDeviceLogs, PhysicalControl, SimulatorControl
from sim_mirror.core.devices import DeviceDirectory, DeviceRef, NoDevice
from sim_mirror.core.status import device_choice, device_choices
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, PhysicalDevice
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.protocol import DeviceChoice, DeviceKind
from sim_mirror.scope import Scope

if TYPE_CHECKING:
    from sim_mirror.core.instance import DeviceInstance

logger = logging.getLogger(__name__)

#: How long a boot may take before it is given up: a first boot migrates data and can take minutes.
BOOT_TIMEOUT_S = 240.0


class DeviceBackend(Protocol):
    """One kind of device, as `DeviceManager` drives it."""

    @property
    def kind(self) -> DeviceKind: ...

    @property
    def counts_toward_max_booted(self) -> bool:
        """Whether its running devices count against ``device.max_booted``: a simulator holds the Mac's memory."""
        ...

    async def choices(self, config: SimConfig, created: Collection[str]) -> list[DeviceChoice]:
        """The devices of this kind a person could pick, for a picker. Raises `DeviceControlError`."""
        ...

    async def lookup(self, udid: str, config: SimConfig) -> DeviceChoice | None:
        """The device a person picked, as a picker lists it; None when there is no such device."""
        ...

    async def resolve(self, scope: Scope, remembered: str | None, config: SimConfig) -> DeviceRef:
        """The scope's device, given the one it remembers. Raises `NoDevice` or `DeviceControlError`."""
        ...

    async def prepare(self, instance: DeviceInstance, config: SimConfig) -> None:
        """Make the device ready for its connector: booted and up, or connected and unlocked."""
        ...

    async def release(self, instance: DeviceInstance, *, shutdown: bool) -> None:
        """What is left to do once the device's session is let go -- shutting it down, when asked and when it can be.
        Never raises: a device that will not shut down is left running, and said so in the log."""
        ...

    def control(self, developer_dir: str) -> DeviceControl:
        """The device's own tool, on the Xcode named."""
        ...

    def developer_dir(self, config: SimConfig) -> str:
        """The Xcode a device of this kind is reached with under these settings."""
        ...


class SimulatorBackend:
    """Simulators, through simctl: listed, made when a scope has none, booted, and shut down when SimMirror may."""

    kind: DeviceKind = "simulator"
    counts_toward_max_booted = True

    def __init__(self, simctl_for: Callable[[str], Simctl], directory: DeviceDirectory) -> None:
        self.simctl_for = simctl_for
        self.directory = directory

    async def choices(self, config: SimConfig, created: Collection[str]) -> list[DeviceChoice]:
        return device_choices(await self.simctl_for(config.developer_dir).devices(), created)

    async def lookup(self, udid: str, config: SimConfig) -> DeviceChoice | None:
        device = await self.simctl_for(config.developer_dir).device(udid)
        return device_choice(device, ()) if device is not None and device.available else None

    async def resolve(self, scope: Scope, remembered: str | None, config: SimConfig) -> DeviceRef:
        return await self.directory.resolve(self.simctl_for(config.developer_dir), scope, config)

    async def prepare(self, instance: DeviceInstance, config: SimConfig) -> None:
        simctl = self.simctl_for(instance.developer_dir)
        device = await simctl.device(instance.udid)
        if device is not None and not device.booted:
            # Before the boot is awaited: a device ended while it boots is still SimMirror's to shut down.
            instance.booted_by_us = True
            await simctl.boot(instance.udid)
        await simctl.bootstatus(instance.udid, timeout=BOOT_TIMEOUT_S)

    async def release(self, instance: DeviceInstance, *, shutdown: bool) -> None:
        if not shutdown:
            return
        try:
            await self.simctl_for(instance.developer_dir).shutdown(instance.udid)
        except SimctlError as exc:
            logger.warning("could not shut down the simulator %s: %s", instance.udid, exc)

    def control(self, developer_dir: str) -> SimulatorControl:
        return SimulatorControl(self.simctl_for(developer_dir))

    def developer_dir(self, config: SimConfig) -> str:
        return config.developer_dir


def runtime_of(device: PhysicalDevice) -> str:
    """What a real device runs, as a picker says it -- with its model, since two phones are often named alike."""
    return f"{device.platform} {device.os_version} · {device.model}"


class PhysicalBackend:
    """Real iPhones and iPads, through devicectl: listed while ``real_devices.enabled``, used only once one is picked,
    never booted or shut down -- a device that is not connected, paired or unlocked is refused with what to do."""

    kind: DeviceKind = "physical"
    counts_toward_max_booted = False

    def __init__(
        self,
        devicectl_for: Callable[[str], Devicectl],
        *,
        copy: HostCopy | None = None,
        logs: DeviceLogs | None = None,
    ) -> None:
        self.devicectl_for = devicectl_for
        self._copy = copy or HostCopy()
        #: What is kept of each attached device's log, for its control to read.
        self.logs: DeviceLogs = logs or NoDeviceLogs()

    def developer_dir(self, config: SimConfig) -> str:
        return config.real_devices_developer_dir or config.developer_dir

    @staticmethod
    def choice(device: PhysicalDevice) -> DeviceChoice:
        """A real device as a picker lists it."""
        return {
            "udid": device.udid,
            "name": device.name,
            "runtime": runtime_of(device),
            "state": "Connected" if device.connected else "Disconnected",
            "created": False,
            "kind": "physical",
            "connection": "usb" if device.connection == "usb" else "network" if device.connection else None,
            "detail": device.detail,
            "usable": device.detail is None,
        }

    async def choices(self, config: SimConfig, created: Collection[str]) -> list[DeviceChoice]:
        if not config.real_devices:
            return []
        devices = await self.devicectl_for(self.developer_dir(config)).devices()
        return sorted((self.choice(device) for device in devices), key=lambda choice: choice["name"])

    async def lookup(self, udid: str, config: SimConfig) -> DeviceChoice | None:
        if not config.real_devices:
            return None
        device = await self.devicectl_for(self.developer_dir(config)).device(udid)
        return None if device is None else self.choice(device)

    async def _usable(self, udid: str, config: SimConfig) -> PhysicalDevice:
        if not is_device_udid(udid):
            raise NoDevice(self._copy.not_connected(udid or "The device"))
        device = await self.devicectl_for(self.developer_dir(config)).device(udid)
        if device is None or not device.connected:
            raise NoDevice(self._copy.not_connected(device.name if device else udid))
        if device.detail is not None:
            raise NoDevice(f"{device.name}: {device.detail}.")
        return device

    async def resolve(self, scope: Scope, remembered: str | None, config: SimConfig) -> DeviceRef:
        if not config.real_devices:
            raise NoDevice(self._copy.kind_unavailable("physical"))
        device = await self._usable(remembered or "", config)
        runtime = runtime_of(device)
        return DeviceRef(
            device.udid, device.name, runtime, False, "physical", "usb" if device.connection == "usb" else "network"
        )

    async def prepare(self, instance: DeviceInstance, config: SimConfig) -> None:
        device = await self._usable(instance.udid, config)
        instance.connection = "usb" if device.connection == "usb" else "network"

    async def release(self, instance: DeviceInstance, *, shutdown: bool) -> None:
        return None

    def control(self, developer_dir: str) -> PhysicalControl:
        return PhysicalControl(self.devicectl_for(developer_dir), self.logs)
