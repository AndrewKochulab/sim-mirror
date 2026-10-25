# SPDX-License-Identifier: Apache-2.0
"""The android connector: an Android emulator or phone, reached through adb.

It drives what adb lists (``kinds`` android), composing the session from roles:

* **screen**: screenshots, a few a second (`screen.AdbScreen`) -- or scrcpy's live H.264 once it is set up;
* **input and text**: adb's ``input`` (`adb_input`) -- or scrcpy's, which is about ten milliseconds a touch;
* **reader**: uiautomator, with the keyboard and system bars as covers (`uiautomator`);
* **rotator**: the display turned and kept turned through the device's settings, put back when it is let go.

Everything else -- apps, the log, how it looks, where it is -- is the backend's (`core.android_backend`). The
session is alive while adb lists the device ready: an emulator closed or a phone unplugged ends it, and recovery
attaches again when it comes back. A phone's session has no ``location``: only an emulator's can be set.

Nothing here starts adb's server or reaches a device until a device is attached: `probe` reads files only.
"""

from __future__ import annotations

import asyncio
import contextlib

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.android.adb_input import AdbInput, AdbText
from sim_mirror.connectors.android.geometry import ROTATIONS, Rotation, screen_of
from sim_mirror.connectors.android.keys import ANDROID_BUTTONS
from sim_mirror.connectors.android.screen import AdbScreen
from sim_mirror.connectors.android.uiautomator import UiAutomatorReader
from sim_mirror.connectors.base import Capability, ConnectorReport, ConnectorUnavailable, DeviceSession
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.core.android_control import AndroidControl
from sim_mirror.core.android_devices import AndroidServices
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.orientation import Orientation
from sim_mirror.platform.adb import Adb, AdbError
from sim_mirror.platform.emulator import port_of
from sim_mirror.protocol import DeviceKind

NAME = "android"
KINDS: frozenset[DeviceKind] = frozenset({"android"})
#: What an Android session does with adb alone.
ADB = frozenset({
    Capability.LIFECYCLE, Capability.DEVICE_LIST, Capability.APPEARANCE, Capability.OPEN_URL, Capability.APP_INSTALL,
    Capability.APP_LAUNCH, Capability.LOGS, Capability.SCREENSHOT, Capability.STREAM_JPEG, Capability.INPUT_TOUCH,
    Capability.INPUT_BUTTON, Capability.INPUT_KEY, Capability.INPUT_TEXT, Capability.ELEMENT_TREE, Capability.RECORD,
    Capability.STATUS_BAR, Capability.ACCESSIBILITY, Capability.ROTATE,
})  # fmt: skip
#: What only an emulator does: it is put somewhere through its console.
EMULATOR_ONLY = frozenset({Capability.LOCATION})
#: The most screenshots a second adb alone shows.
FPS_LIMIT = 4


class TransportWatch:
    """Whether adb still lists a device ready, told as it changes: an emulator closed or a phone unplugged ends its
    session at once, rather than at its next failed screenshot."""

    def __init__(self, adb: Adb, serial: str) -> None:
        self._adb = adb
        self._serial = serial
        self.alive = True
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        try:
            async for listed in self._adb.track():
                if not any(device.serial == self._serial and device.ready for device in listed):
                    break
        except AdbError:
            pass
        self.alive = False

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


class AndroidRotator:
    """Turning the device: its display turned that way and kept turned."""

    def __init__(self, control: AndroidControl, udid: str, rotation: Rotation) -> None:
        self._control = control
        self._udid = udid
        self._rotation = rotation

    async def rotate(self, orientation: Orientation) -> None:
        await self._control.set_rotation(self._udid, ROTATIONS.index(orientation))
        self._rotation.forget()


class AndroidConnector:
    name = NAME
    kinds = KINDS

    def __init__(self, services: AndroidServices, *, copy: HostCopy | None = None) -> None:
        self._services = services
        self._copy = copy or HostCopy()

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not config.android:
            return ConnectorReport(
                NAME, False, reasons=("Android devices are off here (android.enabled).",), kinds=KINDS
            )
        sdk = self._services.sdk(config.android_sdk_path)
        if sdk is None or sdk.adb_version is None:
            return ConnectorReport(
                NAME,
                False,
                reasons=(
                    "No Android SDK with adb was found: install Android Studio, or name the SDK in android.sdk_path.",
                ),
                kinds=KINDS,
            )
        versions = {"adb": sdk.adb_version, **({"emulator": sdk.emulator_version} if sdk.emulator_version else {})}
        return ConnectorReport(NAME, True, ADB | EMULATOR_ONLY, versions, kinds=KINDS)

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        adb = self._services.adb
        try:
            serial = await self._services.directory.serial(udid)
            size = await adb.run(serial, ["wm", "size"])
            density = await adb.run(serial, ["wm", "density"])
            # The lock screen's own state, which ``KeyguardStateMonitor`` reports (measured on API 36).
            locked = "mIsShowing=true" in await adb.run(serial, ["dumpsys", "window", "policy"])
        except AdbError as exc:
            raise ConnectorUnavailable(f"the Android device cannot be reached: {exc}", 409) from exc
        screen = screen_of(size, density)

        async def ask_rotation() -> str:
            return await adb.run(serial, ["dumpsys", "display"])

        rotation = Rotation(ask_rotation)
        control = AndroidControl(adb, self._services.directory.serial, routes=self._services.routes)
        watch = TransportWatch(adb, serial)
        capabilities = ADB | (EMULATOR_ONLY if port_of(serial) is not None else frozenset())
        return DeviceSession(
            connector=NAME,
            capabilities=capabilities,
            screen=AdbScreen(adb, serial, screen, rotation),
            input=AdbInput(adb, serial, screen, rotation),
            reader=UiAutomatorReader(adb, serial, screen),
            text=AdbText(adb, serial),
            rotator=AndroidRotator(control, udid, rotation),
            fps_limit=FPS_LIMIT,
            is_alive=lambda: watch.alive,
            on_close=watch.close,
            note=self._copy.android_limits(scrcpy="set scrcpy up for a live screen and quick touches", locked=locked),
            buttons=ANDROID_BUTTONS,
        )

    async def reap_orphans(self) -> int:
        return 0


def create(context: ConnectorContext) -> AndroidConnector:
    return AndroidConnector(context.android or AndroidServices.build(context.state), copy=context.copy)
