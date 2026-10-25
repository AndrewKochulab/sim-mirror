# SPDX-License-Identifier: Apache-2.0
"""Every Android device this Mac has, by the id SimMirror knows it by: AVDs, the emulators running them, and phones.

An AVD is listed whether it runs or not, by its id (`identifiers.android_id`, ``avd-…``). One that runs is the
emulator adb lists as ``emulator-<port>``, found by asking each running emulator which AVD it is
(``ro.boot.qemu.avd_name``). A phone is listed by its serial number (``ro.serialno``, ``adb-…``) -- the same one
whether it is reached by cable or over Wi-Fi -- or, while it has not allowed this Mac to debug it and cannot be asked,
by adb's serial for it. What each running device says of itself is asked once per adb connection to it (its
``transport_id``), so listing again is one request to the adb server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sim_mirror.platform.adb import Adb, AdbDevice, AdbError
from sim_mirror.platform.android_sdk import AndroidSdk, Avd, avds, find_sdk
from sim_mirror.platform.emulator import Emulators
from sim_mirror.platform.identifiers import EMULATOR_PREFIX, PHONE_PREFIX, android_id
from sim_mirror.seams import StateStore

#: How long asking a running device about itself may take.
ASK_TIMEOUT_S = 5.0

Role = Literal["phone", "emulator"]


@dataclass(frozen=True)
class AndroidDevice:
    """An Android device as a picker lists it, and how it is reached now."""

    id: str
    name: str
    role: Role
    #: adb's serial for it while it runs or is connected; None for an AVD not running.
    serial: str | None
    #: adb's state for it -- ``device``, ``offline``, ``unauthorized`` -- or ``stopped`` for an AVD not running.
    state: str
    #: What it runs, as a picker says it: ``Android 16 (API 36)``.
    runtime: str
    #: How a phone reaches this Mac; None for an emulator.
    connection: Literal["usb", "network"] | None = None
    #: Why it cannot be used now; None when it can.
    detail: str | None = None
    avd: Avd | None = None

    @property
    def running(self) -> bool:
        return self.serial is not None and self.state == "device"


@dataclass(frozen=True)
class _Said:
    """What a running device said of itself, kept for its adb connection."""

    transport: str
    name: str
    release: str
    sdk: str


def runtime_label(release: str, sdk: str) -> str:
    """What an Android device runs: ``Android 16 (API 36)``."""
    if release and sdk:
        return f"Android {release} (API {sdk})"
    return f"Android {release}" if release else f"API {sdk}" if sdk else "Android"


#: What adb's states mean to a person, for one that cannot be used.
STATE_DETAILS = {
    "unauthorized": "allow USB debugging on the phone, then try again",
    "offline": "it cannot be reached now: unplug it and plug it in again",
    "authorizing": "the phone is being asked to allow USB debugging",
    "connecting": "it is still being connected to",
    "no permissions": "this Mac has no permission to use it",
}


class AndroidDirectory:
    """The Android devices this Mac has, listed from the AVDs and adb."""

    def __init__(self, adb: Adb, *, avds_of: Callable[[AndroidSdk | None], list[Avd]] = avds) -> None:
        self.adb = adb
        self._avds_of = avds_of
        self._said: dict[str, _Said] = {}

    async def _ask(self, device: AdbDevice, key: str) -> _Said | None:
        """What a running device says of itself: its AVD's name or its serial number, and what it runs."""
        transport = device.properties.get("transport_id", "")
        known = self._said.get(device.serial)
        if known is not None and known.transport == transport and transport:
            return known
        script = f"getprop {key}; getprop ro.build.version.release; getprop ro.build.version.sdk"
        try:
            said = await self.adb.run(device.serial, script, timeout=ASK_TIMEOUT_S)
        except (AdbError, asyncio.TimeoutError, TimeoutError):
            return None
        lines = [*said.splitlines(), "", "", ""]
        found = _Said(transport, lines[0].strip(), lines[1].strip(), lines[2].strip())
        if not found.name:
            return None
        self._said[device.serial] = found
        return found

    async def devices(self, sdk: AndroidSdk | None, *, phones: bool) -> list[AndroidDevice]:
        """Every AVD, running or not -- what `sdk` has to run it -- then the phones adb reaches when `phones` says to
        list them."""
        listed = await self.adb.devices()
        running: dict[str, tuple[AdbDevice, _Said]] = {}
        found: list[AndroidDevice] = []
        for device in listed:
            if device.connection != "emulator" or not device.ready:
                continue
            said = await self._ask(device, "ro.boot.qemu.avd_name")
            if said is not None:
                running[said.name] = (device, said)
        for avd in self._avds_of(sdk):
            device_said = running.pop(avd.name, None)
            found.append(_emulator(avd.name, avd, *device_said) if device_said else _stopped(avd))
        # An emulator of an AVD kept somewhere else -- another AVD folder -- is listed by its name alone.
        found += [_emulator(name, None, device, said) for name, (device, said) in running.items()]
        if phones:
            for device in listed:
                if device.connection == "emulator":
                    continue
                found.append(await self._phone(device))
        return found

    async def _phone(self, device: AdbDevice) -> AndroidDevice:
        said = await self._ask(device, "ro.serialno") if device.ready else None
        connection: Literal["usb", "network"] = "network" if device.connection == "network" else "usb"
        name = device.model or device.serial
        if said is None:
            detail = STATE_DETAILS.get(device.state, f"it is {device.state}") if not device.ready else None
            return AndroidDevice(
                android_id(PHONE_PREFIX, device.serial),
                name,
                "phone",
                device.serial,
                device.state,
                "Android",
                connection,
                detail or "it does not answer adb now",
            )
        return AndroidDevice(
            android_id(PHONE_PREFIX, said.name),
            name,
            "phone",
            device.serial,
            device.state,
            runtime_label(said.release, said.sdk),
            connection,
        )

    async def find(self, udid: str, sdk: AndroidSdk | None, *, phones: bool) -> AndroidDevice | None:
        """The device with this id, as it is now; None when there is none."""
        return next((device for device in await self.devices(sdk, phones=phones) if device.id == udid), None)

    async def serial(self, udid: str) -> str:
        """adb's serial for a running device -- or `AdbError` when it is not running or reachable now."""
        found = await self.find(udid, None, phones=True)
        if found is None or not found.running or found.serial is None:
            raise AdbError(f"{found.name if found else udid} is not running or not reachable through adb now")
        return found.serial

    def forget(self, serial: str) -> None:
        """Let go of what a device said: it was restarted, or its connection changed."""
        self._said.pop(serial, None)


def _emulator(name: str, avd: Avd | None, device: AdbDevice, said: _Said) -> AndroidDevice:
    return AndroidDevice(
        android_id(EMULATOR_PREFIX, name),
        avd.display_name if avd else name.replace("_", " "),
        "emulator",
        device.serial,
        device.state,
        runtime_label(said.release, said.sdk),
        avd=avd,
    )


def _stopped(avd: Avd) -> AndroidDevice:
    return AndroidDevice(
        android_id(EMULATOR_PREFIX, avd.name),
        avd.display_name,
        "emulator",
        None,
        "stopped",
        f"API {avd.api}" if avd.api else "Android",
        detail=avd.problem,
        avd=avd,
    )


@dataclass
class AndroidServices:
    """What every part that reaches an Android device shares: one adb client, the devices it lists, the emulators
    this host started, and the routes being driven."""

    adb: Adb
    directory: AndroidDirectory
    emulators: Emulators
    routes: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    #: ``android.sdk_path`` as last read: what adb's server is started from.
    sdk_path: str = ""

    @classmethod
    def build(cls, state: StateStore, adb: Adb | None = None) -> AndroidServices:
        """The services for a host, with its folders: emulators' pid files under its run folder, their logs in its
        log folder."""
        holder: list[AndroidServices] = []

        def adb_path() -> Path | None:
            sdk = holder[0].sdk() if holder else None
            return sdk.adb if sdk else None

        reached = adb or Adb(adb_path=adb_path)
        services = cls(
            reached, AndroidDirectory(reached), Emulators(state.run_dir() / "android", state.log_dir(), state.owner_tag)
        )
        holder.append(services)
        return services

    def sdk(self, configured: str | None = None) -> AndroidSdk | None:
        """The SDK these settings name -- remembered, so adb's server is started from it -- else the last one named."""
        if configured is not None:
            self.sdk_path = configured
        return find_sdk(self.sdk_path)
