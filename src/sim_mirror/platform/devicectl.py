# SPDX-License-Identifier: Apache-2.0
"""devicectl, the tool Xcode reaches a real iPhone or iPad with, as SimMirror calls it.

Every call runs ``xcrun devicectl -q ... --json-output -`` on the scope's Xcode and reads the JSON devicectl promises to
keep stable, never its table. Its answers carry both the ``properties`` dictionary and, until a later release drops
them, the deprecated ``hardwareProperties``, ``deviceProperties`` and ``connectionProperties``: each value is read from
the first and then the second, so either alone is enough. A device is named by its hardware UDID, which devicectl takes
wherever it takes a device.

Nothing here pairs, unpairs, changes trust or touches a passcode: a device the Mac has not been trusted by is listed,
and said to be unusable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.platform.xcrun import XcrunResult, XcrunRunner, run_xcrun

_BUNDLE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.-]{0,254}\Z")
#: How long a call that installs an app may take.
INSTALL_TIMEOUT_S = 600.0
#: The platforms a real device SimMirror drives runs.
PLATFORMS = ("iOS", "iPadOS")
MODES = ("light", "dark")
#: What devicectl's JSON says of a feature a device has, before its name.
FEATURE_PREFIX = "com.apple.coredevice.feature."


class DevicectlError(DeviceControlError):
    """A devicectl call that did not do what it was asked, with why as devicectl said it."""


@dataclass(frozen=True)
class PhysicalDevice:
    """A real device this Mac knows."""

    udid: str
    #: CoreDevice's own identifier for it, which devicectl's answers name it by.
    identifier: str
    name: str
    model: str
    platform: str
    os_version: str
    #: ``wired``, ``localNetwork``, or what devicectl says.
    transport: str
    paired: bool
    connected: bool
    #: Whether Developer Mode is on; None when devicectl does not say.
    developer_mode: bool | None
    #: What the device can do, by devicectl's feature names without their prefix.
    features: frozenset[str]

    @property
    def connection(self) -> str | None:
        """How it reaches this Mac, as the protocol says it: ``usb``, ``network``, or None when it does not."""
        if not self.connected:
            return None
        return "usb" if self.transport == "wired" else "network"

    @property
    def detail(self) -> str | None:
        """Why it cannot be used now, said so a person can act on it; None when it can."""
        if not self.paired:
            return "Not paired: unlock it and choose Trust"
        if not self.connected:
            return "Not connected"
        if self.developer_mode is False:
            return "Developer Mode off"
        return None


@dataclass(frozen=True)
class Display:
    """A device's main screen: its pixels, the scale of its points, and which way up it is."""

    width_px: int
    height_px: int
    scale: float
    orientation: str


@dataclass(frozen=True)
class LockState:
    passcode_required: bool
    unlocked_since_boot: bool


@dataclass(frozen=True)
class App:
    bundle_id: str
    name: str
    #: Where the app is on the device, as a ``file://`` URL of its bundle.
    url: str
    version: str


@dataclass(frozen=True)
class Process:
    pid: int
    #: Where its executable is on the device, as a ``file://`` URL.
    executable: str


def _get(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, Mapping):
            return None
        data = data.get(key)
    return data


def _first(data: Any, *paths: Sequence[str]) -> Any:
    """The first of these paths that says something."""
    for path in paths:
        found = _get(data, *path)
        if found is not None:
            return found
    return None


def _developer_mode(entry: Any) -> bool | None:
    status = _first(entry, ("properties", "state", "developerModeStatus"), ("deviceProperties", "developerModeStatus"))
    if isinstance(status, Mapping):
        return "enabled" in status if status else None
    return {"enabled": True, "disabled": False}.get(str(status)) if status is not None else None


def read_device(entry: Any) -> PhysicalDevice | None:
    """A device from devicectl's list, or None for one SimMirror does not drive -- a simulator, a Mac, a watch."""
    reality = _first(entry, ("properties", "hardware", "reality"), ("hardwareProperties", "reality"))
    platform = _first(entry, ("properties", "hardware", "platform"), ("hardwareProperties", "platform"))
    udid = _first(entry, ("properties", "hardware", "udid"), ("hardwareProperties", "udid"))
    if reality != "physical" or platform not in PLATFORMS or not is_device_udid(udid):
        return None
    version = _first(
        entry, ("properties", "software", "osVersionNumber", "stringValue"), ("deviceProperties", "osVersionNumber")
    )
    state = _first(entry, ("properties", "connection", "state"), ("connectionProperties", "tunnelState"))
    features = {
        str(feature["featureIdentifier"]).removeprefix(FEATURE_PREFIX)
        for feature in entry.get("capabilities") or ()
        if isinstance(feature, Mapping) and "featureIdentifier" in feature
    }
    return PhysicalDevice(
        udid=str(udid),
        identifier=str(entry.get("identifier", "")),
        name=str(_first(entry, ("properties", "state", "name"), ("deviceProperties", "name")) or udid),
        model=str(
            _first(entry, ("properties", "hardware", "marketingName"), ("hardwareProperties", "marketingName"))
            or "iPhone"
        ),
        platform=str(platform),
        os_version=str(version or ""),
        transport=str(
            _first(entry, ("properties", "connection", "transportType"), ("connectionProperties", "transportType"))
            or ""
        ),
        paired=_first(entry, ("properties", "connection", "pairingState"), ("connectionProperties", "pairingState"))
        == "paired",
        connected=state == "connected",
        developer_mode=_developer_mode(entry),
        features=frozenset(features),
    )


def _udid(udid: str) -> str:
    if not is_device_udid(udid):
        raise DevicectlError(f"not a real device's id: {udid!r}")
    return udid


def _bundle_id(bundle_id: str) -> str:
    if not isinstance(bundle_id, str) or not _BUNDLE_ID.match(bundle_id):
        raise DevicectlError(f"not a bundle identifier: {bundle_id!r}")
    return bundle_id


def _text(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("-") or "\x00" in value:
        raise DevicectlError(f"not a usable {what}: {value!r}")
    return value


def _said(document: Any) -> str:
    """What devicectl said went wrong, from its JSON's error."""
    info = _get(document, "error", "userInfo")
    parts = [
        _get(info, key, "string")
        for key in ("NSLocalizedDescription", "NSLocalizedFailureReason", "NSLocalizedRecoverySuggestion")
    ]
    return " ".join(str(part) for part in parts if part)


class Devicectl:
    """devicectl, on the Xcode a scope chose."""

    def __init__(self, runner: XcrunRunner = run_xcrun, *, developer_dir: str = "") -> None:
        self._runner = runner
        self._developer_dir = developer_dir

    @property
    def developer_dir(self) -> str:
        return self._developer_dir

    async def _call(self, *args: str, timeout: float = 30.0, input_data: bytes | None = None) -> Any:
        """The ``result`` of a devicectl call's JSON; raises with what devicectl said when it did not succeed."""
        result: XcrunResult = await self._runner(
            "devicectl",
            "-q",
            *args,
            "--json-output",
            "-",
            timeout=timeout,
            developer_dir=self._developer_dir,
            input_data=input_data,
        )
        try:
            document = json.loads(result.out) if result.out.strip() else None
        except ValueError:
            document = None
        outcome = _get(document, "info", "outcome")
        if result.ok and outcome == "success":
            return _get(document, "result")
        said = _said(document) or result.message
        raise DevicectlError(f"devicectl {' '.join(args[:3])}: {said}", result)

    # -- what there is -------------------------------------------------------------------------------------------------

    async def devices(self) -> list[PhysicalDevice]:
        """The iPhones and iPads this Mac knows, connected or not."""
        found = await self._call("list", "devices")
        entries = _get(found, "devices") or []
        return [device for device in (read_device(entry) for entry in entries) if device is not None]

    async def device(self, udid: str) -> PhysicalDevice | None:
        _udid(udid)
        return next((device for device in await self.devices() if device.udid == udid), None)

    async def display(self, udid: str) -> Display:
        """The device's main screen."""
        found = await self._call("device", "info", "displays", "--device", _udid(udid))
        displays = _get(found, "displays") or []
        main = next((entry for entry in displays if entry.get("primary")), displays[0] if displays else None)
        size = _get(main, "nativeSize")
        scale = _get(main, "pointScale")
        if not (isinstance(size, list) and len(size) == 2 and isinstance(scale, int | float) and scale > 0):
            raise DevicectlError("devicectl did not say how large the device's screen is")
        orientation = _get(found, "orientation", "currentDeviceNonFlatOrientation")
        return Display(int(size[0]), int(size[1]), float(scale), str(orientation or "portrait"))

    async def lock_state(self, udid: str) -> LockState:
        found = await self._call("device", "info", "lockState", "--device", _udid(udid))
        return LockState(bool(_get(found, "passcodeRequired")), bool(_get(found, "unlockedSinceBoot")))

    async def appearance(self, udid: str) -> dict[str, Any]:
        """How the device looks now: its style, text size, contrast and motion, as devicectl says them."""
        found = await self._call("device", "info", "appearance", "--device", _udid(udid))
        return dict(found) if isinstance(found, Mapping) else {}

    async def apps(self, udid: str, *, bundle_id: str | None = None) -> list[App]:
        """The apps a developer installed -- or, by its bundle id, any app, the device's own too."""
        wanted = ("--bundle-id", _bundle_id(bundle_id)) if bundle_id is not None else ()
        found = await self._call("device", "info", "apps", "--device", _udid(udid), *wanted)
        return [
            App(
                str(app["bundleIdentifier"]),
                str(app.get("name", "")),
                str(app.get("url", "")),
                str(app.get("version", "")),
            )
            for app in _get(found, "apps") or []
            if isinstance(app, Mapping) and "bundleIdentifier" in app
        ]

    async def processes(self, udid: str) -> list[Process]:
        found = await self._call("device", "info", "processes", "--device", _udid(udid))
        return [
            Process(int(entry["processIdentifier"]), str(entry.get("executable", "")))
            for entry in _get(found, "runningProcesses") or []
            if isinstance(entry, Mapping) and isinstance(entry.get("processIdentifier"), int)
        ]

    # -- apps ----------------------------------------------------------------------------------------------------------

    async def install(self, udid: str, app_path: str) -> None:
        await self._call(
            "device", "install", "app", "--device", _udid(udid), _text(app_path, "app path"), timeout=INSTALL_TIMEOUT_S
        )

    async def uninstall(self, udid: str, bundle_id: str) -> None:
        await self._call("device", "uninstall", "app", "--device", _udid(udid), _bundle_id(bundle_id))

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        """Launch an app, answering its pid; `terminate_running` ends a running copy first."""
        for arg in args:
            if not isinstance(arg, str) or "\x00" in arg:
                raise DevicectlError(f"not a usable launch argument: {arg!r}")
        fresh = ("--terminate-existing",) if terminate_running else ()
        found = await self._call(
            "device", "process", "launch", "--device", _udid(udid), *fresh, _bundle_id(bundle_id), *args, timeout=60.0
        )
        pid = _get(found, "process", "processIdentifier")
        return int(pid) if isinstance(pid, int) else None

    async def terminate(self, udid: str, pid: int) -> None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise DevicectlError(f"not a process id: {pid!r}")
        await self._call("device", "process", "terminate", "--device", _udid(udid), "--pid", str(pid))

    async def open_url(self, udid: str, url: str) -> None:
        await self._call("device", "process", "openURL", "--device", _udid(udid), _text(url, "URL"))

    async def pbcopy(self, udid: str, text: str) -> None:
        """Put text on the device's pasteboard."""
        await self._call("device", "pasteboard", "copy", "--device", _udid(udid), input_data=text.encode("utf-8"))

    # -- how it looks and where it is --------------------------------------------------------------------------------

    async def set_appearance(self, udid: str, **settings: str) -> None:
        """Change how the device looks, by devicectl's own option names: ``mode``, ``text-size``,
        ``increase-contrast``, ``reduce-motion``."""
        options: list[str] = []
        for name, value in settings.items():
            flag = name.replace("_", "-")
            if flag not in APPEARANCE_OPTIONS or not isinstance(value, str) or value not in APPEARANCE_OPTIONS[flag]:
                raise DevicectlError(f"not an appearance setting: {flag} {value!r}")
            options += [f"--{flag}", value]
        if not options:
            raise DevicectlError("an appearance change needs a setting")
        await self._call("device", "settings", "appearance", "--device", _udid(udid), *options)

    async def locate(self, udid: str, latitude: float, longitude: float) -> None:
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise DevicectlError(f"not a place: {latitude!r}, {longitude!r}")
        await self._call(
            "device",
            "simulate",
            "location",
            "coordinate",
            "--device",
            _udid(udid),
            "--latitude",
            f"{latitude:.6f}",
            "--longitude",
            f"{longitude:.6f}",
        )

    async def route(self, udid: str, route_file: Path) -> None:
        """Move the device along the route a JSON file describes (``mode``, ``speed``, ``waypoints``)."""
        await self._call(
            "device", "simulate", "location", "route", "--device", _udid(udid), "--route-file", str(route_file)
        )

    async def clear_location(self, udid: str) -> None:
        await self._call("device", "simulate", "location", "clear", "--device", _udid(udid))

    async def screenshot(self, udid: str, destination: Path) -> None:
        """Write a PNG of the device's screen to `destination`."""
        await self._call("device", "capture", "screenshot", "--device", _udid(udid), "--destination", str(destination))


#: What `Devicectl.set_appearance` may change, and the values each takes.
APPEARANCE_OPTIONS: Mapping[str, tuple[str, ...]] = {
    "mode": MODES,
    "text-size": (
        "extra-small",
        "small",
        "medium",
        "large",
        "extra-large",
        "extra-extra-large",
        "extra-extra-extra-large",
        "accessibility-medium",
        "accessibility-large",
        "accessibility-extra-large",
        "accessibility-extra-extra-large",
        "accessibility-extra-extra-extra-large",
    ),
    "increase-contrast": ("on", "off"),
    "reduce-motion": ("on", "off"),
}


def text_size_name(said: object) -> str | None:
    """A text size as devicectl's appearance says it (``Large``, ``Accessibility Medium``) named as it is set."""
    name = str(said).strip().lower().replace(" ", "-") if isinstance(said, str) else ""
    return name if name in APPEARANCE_OPTIONS["text-size"] else None
