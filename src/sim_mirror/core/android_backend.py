# SPDX-License-Identifier: Apache-2.0
"""Android devices as a kind `DeviceManager` drives: emulators on this Mac, and the phones adb reaches.

**An emulator** runs here, like a simulator: it counts against ``device.max_booted``, an AVD that is not running is
started for the scope that asks for it -- headless, on a console port no other emulator has -- and booted, and one this
host started is shut down when it is let go as a simulator would be. One a person started, from Android Studio say, is
used as it is and left running; a restarted daemon tells the two apart by the pid files the start left (`Emulators`).

**A phone** is a real device: listed while ``android.phones`` lets it, used only once a person picks it -- or an agent,
while ``android.agents_choose`` lets it -- never started or shut down, and what SimMirror changes on it is put back when
it is let go. A phone that has not allowed this Mac to debug it is listed and cannot be picked, saying what to do.

Nothing is listed while ``android.enabled`` is off. What is asked of a running device is `AndroidControl`'s.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import TYPE_CHECKING

from sim_mirror.config.model import SimConfig
from sim_mirror.core.android_control import AndroidControl
from sim_mirror.core.android_devices import AndroidDevice, AndroidServices
from sim_mirror.core.backends import Placed
from sim_mirror.core.devices import DeviceRef, NoDevice
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.adb import AdbError
from sim_mirror.platform.android_sdk import AndroidSdk
from sim_mirror.platform.emulator import EmulatorConsole, EmulatorError, EmulatorOptions, free_port, port_of
from sim_mirror.protocol import DeviceChoice, DeviceKind
from sim_mirror.scope import Scope

if TYPE_CHECKING:
    from sim_mirror.core.instance import DeviceInstance

logger = logging.getLogger(__name__)

#: How often a booting emulator is asked whether it is up.
BOOT_POLL_S = 1.0
#: What a picker says of a device by adb's state for it.
STATES = {"device": "Connected", "stopped": "Shutdown"}


def choice_of(device: AndroidDevice) -> DeviceChoice:
    """An Android device as a picker lists it."""
    state = "Booted" if device.role == "emulator" and device.running else STATES.get(device.state, device.state.title())
    return {
        "udid": device.id,
        "name": device.name,
        "runtime": device.runtime,
        "state": state,
        "created": False,
        "kind": "android",
        "connection": device.connection,
        "detail": device.detail,
        "usable": device.detail is None,
    }


class AndroidBackend:
    """Android emulators and phones, through adb and the emulator."""

    kind: DeviceKind = "android"

    def __init__(
        self,
        services: AndroidServices,
        *,
        copy: HostCopy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        console_for: Callable[[int], EmulatorConsole] = EmulatorConsole,
    ) -> None:
        self.services = services
        self._copy = copy or HostCopy()
        self._clock = clock
        self._sleep = sleep
        self._console_for = console_for

    def runs_here(self, device: Placed) -> bool:
        """An emulator runs here; a phone, reached by cable or network, does not."""
        return device.connection is None

    def withheld(self, instance: DeviceInstance, config: SimConfig) -> str | None:
        if not config.android or (not self.runs_here(instance) and not config.android_phones):
            return self._copy.kind_unavailable("android")
        return None

    def developer_dir(self, config: SimConfig) -> str:
        """The Android SDK the device is reached with, as `device.developer_dir` names an Xcode: a session moves when
        the setting does."""
        sdk = self.services.sdk(config.android_sdk_path)
        return str(sdk.root) if sdk else ""

    def _sdk(self, config: SimConfig) -> AndroidSdk | None:
        return self.services.sdk(config.android_sdk_path)

    async def _listed(self, config: SimConfig) -> list[AndroidDevice]:
        if not config.android:
            return []
        sdk = self._sdk(config)
        await self.services.adb.ensure_server()
        return await self.services.directory.devices(sdk, phones=config.android_phones)

    async def choices(self, config: SimConfig, created: Collection[str]) -> list[DeviceChoice]:
        return [choice_of(device) for device in await self._listed(config)]

    async def lookup(self, udid: str, config: SimConfig) -> DeviceChoice | None:
        found = next((device for device in await self._listed(config) if device.id == udid), None)
        return None if found is None else choice_of(found)

    async def resolve(self, scope: Scope, remembered: str | None, config: SimConfig) -> DeviceRef:
        if not config.android:
            raise NoDevice(self._copy.kind_unavailable("android"))
        listed = await self._listed(config)
        found = _chosen(listed, remembered)
        if found is None:
            raise NoDevice(
                self._copy.no_such_device("android")
                if remembered
                else "No Android emulator here can start: make one in Android Studio's Device Manager."
            )
        if found.detail is not None:
            raise NoDevice(f"{found.name}: {found.detail}.")
        return DeviceRef(found.id, found.name, found.runtime, False, "android", found.connection)

    async def prepare(self, instance: DeviceInstance, config: SimConfig) -> None:
        found = await self.services.directory.find(instance.udid, self._sdk(config), phones=config.android_phones)
        if found is None:
            raise NoDevice(self._copy.no_such_device("android"))
        instance.connection = found.connection
        if found.role == "phone":
            if not found.running:
                raise NoDevice(self._copy.not_connected(found.name))
        elif not found.running:
            found = await self._boot(instance, found, config)
        elif found.avd is not None:
            instance.booted_by_us = await self.services.emulators.started_here(found.avd.name) is not None
        instance.runtime = found.runtime
        assert found.serial is not None
        await self._wake(found.serial)

    async def _boot(self, instance: DeviceInstance, device: AndroidDevice, config: SimConfig) -> AndroidDevice:
        """Start an AVD and wait for Android to have booted on it."""
        sdk = self._sdk(config)
        if sdk is None or device.avd is None:
            raise NoDevice(
                "No Android SDK was found to start the emulator: set android.sdk_path, or install Android Studio."
            )
        used = {port for listed in await self.services.adb.devices() if (port := port_of(listed.serial)) is not None}
        options = EmulatorOptions(
            window=config.android_emulator_window,
            gpu=config.android_emulator_gpu,
            cold_boot=config.android_emulator_cold_boot,
        )
        # Before the boot is awaited: an emulator ended while it boots is still SimMirror's to shut down.
        instance.booted_by_us = True
        started = await self.services.emulators.start(sdk.emulator, device.avd.name, free_port(used), options)
        deadline = self._clock() + config.android_boot_timeout
        while self._clock() < deadline:
            if await self._booted(started.serial):
                found = await self.services.directory.find(instance.udid, sdk, phones=False)
                if found is not None and found.running:
                    return found
            await self._sleep(BOOT_POLL_S)
        raise EmulatorError(
            f"{device.name} did not boot within {config.android_boot_timeout}s (android.emulator.boot_timeout): see "
            "the emulator's log in SimMirror's log folder"
        )

    async def _booted(self, serial: str) -> bool:
        """Whether Android has booted on a device: its boot finished, and its package manager answers."""
        adb = self.services.adb
        try:
            boot = await adb.shell(serial, ["getprop", "sys.boot_completed"])
            return boot.text.strip() == "1" and (await adb.shell(serial, ["pm", "path", "android"])).code == 0
        except (AdbError, asyncio.TimeoutError, TimeoutError):
            return False

    async def _wake(self, serial: str) -> None:
        """Turn the screen on and dismiss a lock screen that has no passcode; one that has stays, and is not typed."""
        for command in (["input", "keyevent", "KEYCODE_WAKEUP"], ["wm", "dismiss-keyguard"]):
            with contextlib.suppress(AdbError, asyncio.TimeoutError, TimeoutError):
                await self.services.adb.shell(serial, command)

    async def release(self, instance: DeviceInstance, *, shutdown: bool) -> None:
        route = self.services.routes.pop(instance.udid, None)
        if route is not None:
            route.cancel()
        if not shutdown or not self.runs_here(instance):
            return
        try:
            serial = await self.services.directory.serial(instance.udid)
        except AdbError:
            return
        port = port_of(serial)
        assert port is not None, "an emulator's serial names its console port"
        await self._console_for(port).kill()
        found = await self.services.directory.find(instance.udid, None, phones=False)
        if found is not None and found.avd is not None:
            self.services.emulators.forget(found.avd.name)

    async def gone(self, instances: Sequence[DeviceInstance]) -> list[DeviceInstance]:
        """Those no longer running or reachable -- an emulator closed, a phone unplugged -- asked of adb once."""
        try:
            listed = await self.services.directory.devices(None, phones=True)
        except AdbError as exc:
            logger.warning("could not tell whether the Android devices are still there: %s", exc)
            return []
        running = {device.id for device in listed if device.running}
        return [instance for instance in instances if instance.udid not in running]

    def control(self, developer_dir: str) -> AndroidControl:
        return AndroidControl(
            self.services.adb,
            self.services.directory.serial,
            routes=self.services.routes,
            console_for=self._console_for,
            sleep=self._sleep,
        )


def _chosen(listed: Sequence[AndroidDevice], remembered: str | None) -> AndroidDevice | None:
    """The remembered device; with none remembered, a running emulator, else the first that can start."""
    if remembered:
        return next((device for device in listed if device.id == remembered), None)
    emulators = [device for device in listed if device.role == "emulator"]
    running = [device for device in emulators if device.running]
    usable = [device for device in emulators if device.detail is None]
    return next(iter(running or usable), None)
