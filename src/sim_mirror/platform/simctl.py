# SPDX-License-Identifier: Apache-2.0
"""Typed calls on ``xcrun simctl``, each one argument vector through `xcrun.run_xcrun`.

A call that fails raises `SimctlError` carrying the line simctl printed -- except where the failure *is* the state that
was asked for: booting a device that is already booted, shutting down one that is already off, terminating an app that
is not running. Those succeed.

Every device id, bundle id and URL is checked before it reaches the argv, so nothing that starts with a dash is ever
read by simctl as an option.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sim_mirror.platform.xcrun import XcrunResult, XcrunRunner, run_xcrun

_UDID = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
_BUNDLE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.-]{0,254}\Z")
_PID = re.compile(r":\s*(\d+)\s*\Z")

BOOTED = "Booted"
APPEARANCES = ("light", "dark")
SCREENSHOT_TYPES = ("png", "jpeg")


class SimctlError(Exception):
    """A simctl call that did not do what it was asked, with what simctl said."""

    def __init__(self, message: str, result: XcrunResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class Device:
    udid: str
    name: str
    state: str
    runtime_id: str
    device_type_id: str
    available: bool

    @property
    def booted(self) -> bool:
        return self.state == BOOTED


@dataclass(frozen=True)
class DeviceType:
    identifier: str
    name: str
    product_family: str


@dataclass(frozen=True)
class Runtime:
    identifier: str
    name: str
    version: str
    platform: str
    available: bool
    #: What this runtime can run, newest first -- the order simctl lists them in.
    device_types: tuple[DeviceType, ...]

    @property
    def version_key(self) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", self.version))


def runtime_label(runtime_id: str) -> str:
    """``com.apple.CoreSimulator.SimRuntime.iOS-26-5`` as a person says it: ``iOS 26.5``."""
    tail = runtime_id.rsplit(".", 1)[-1]
    platform, _, version = tail.partition("-")
    return f"{platform} {version.replace('-', '.')}".strip() if version else tail


def is_udid(value: object) -> bool:
    return isinstance(value, str) and bool(_UDID.match(value))


def _udid(udid: str) -> str:
    if not is_udid(udid):
        raise SimctlError(f"not a simulator device id: {udid!r}")
    return udid


def _bundle_id(bundle_id: str) -> str:
    if not isinstance(bundle_id, str) or not _BUNDLE_ID.match(bundle_id):
        raise SimctlError(f"not a bundle identifier: {bundle_id!r}")
    return bundle_id


def _text(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("-") or "\x00" in value:
        raise SimctlError(f"not a usable {what}: {value!r}")
    return value


class Simctl:
    """simctl, on the Xcode a scope chose."""

    def __init__(self, runner: XcrunRunner = run_xcrun, *, developer_dir: str = "") -> None:
        self._runner = runner
        self._developer_dir = developer_dir

    @property
    def developer_dir(self) -> str:
        return self._developer_dir

    async def _run(
        self, *args: str, timeout: float = 30.0, input_data: bytes | None = None, tolerate: Sequence[str] = ()
    ) -> XcrunResult:
        result = await self._runner(
            "simctl", *args, timeout=timeout, developer_dir=self._developer_dir, input_data=input_data
        )
        if result.ok or any(marker in result.err for marker in tolerate):
            return result
        raise SimctlError(f"simctl {args[0]}: {result.message}", result)

    async def _list(self, what: str) -> dict[str, Any]:
        result = await self._run("list", what, "-j")
        try:
            data = json.loads(result.out)
        except ValueError as exc:
            raise SimctlError(f"simctl list {what} did not answer with JSON", result) from exc
        if not isinstance(data, dict):
            raise SimctlError(f"simctl list {what} did not answer with an object", result)
        return data

    # -- what there is ----------------------------------------------------------------------------------------------

    async def devices(self) -> list[Device]:
        found: list[Device] = []
        for runtime_id, entries in (await self._list("devices")).get("devices", {}).items():
            for entry in entries or []:
                found.append(
                    Device(
                        udid=str(entry.get("udid", "")),
                        name=str(entry.get("name", "")),
                        state=str(entry.get("state", "")),
                        runtime_id=str(runtime_id),
                        device_type_id=str(entry.get("deviceTypeIdentifier", "")),
                        available=bool(entry.get("isAvailable", False)),
                    )
                )
        return found

    async def device(self, udid: str) -> Device | None:
        _udid(udid)
        return next((device for device in await self.devices() if device.udid == udid), None)

    async def runtimes(self) -> list[Runtime]:
        found: list[Runtime] = []
        for entry in (await self._list("runtimes")).get("runtimes", []) or []:
            types = tuple(
                DeviceType(
                    identifier=str(kind.get("identifier", "")),
                    name=str(kind.get("name", "")),
                    product_family=str(kind.get("productFamily", "")),
                )
                for kind in entry.get("supportedDeviceTypes") or []
            )
            found.append(
                Runtime(
                    identifier=str(entry.get("identifier", "")),
                    name=str(entry.get("name", "")),
                    version=str(entry.get("version", "")),
                    platform=str(entry.get("platform", "")),
                    available=bool(entry.get("isAvailable", False)),
                    device_types=types,
                )
            )
        return found

    # -- a device's life ----------------------------------------------------------------------------------------------

    async def create(self, name: str, device_type_id: str, runtime_id: str) -> str:
        result = await self._run(
            "create",
            _text(name, "device name"),
            _text(device_type_id, "device type"),
            _text(runtime_id, "runtime"),
            timeout=60.0,
        )
        udid = result.out.strip()
        if not is_udid(udid):
            raise SimctlError(f"simctl create answered with no device id: {result.message}", result)
        return udid

    async def boot(self, udid: str) -> None:
        await self._run("boot", _udid(udid), timeout=120.0, tolerate=("current state: Booted",))

    async def bootstatus(self, udid: str, *, timeout: float = 180.0) -> None:
        """Wait until the device has finished booting -- SpringBoard up, not merely "Booted"."""
        await self._run("bootstatus", _udid(udid), "-b", timeout=timeout)

    async def shutdown(self, udid: str) -> None:
        await self._run("shutdown", _udid(udid), timeout=60.0, tolerate=("current state: Shutdown",))

    async def delete(self, udid: str) -> None:
        await self._run("delete", _udid(udid), timeout=60.0)

    # -- apps and the screen ------------------------------------------------------------------------------------------

    async def install(self, udid: str, app_path: str) -> None:
        await self._run("install", _udid(udid), _text(app_path, "app path"), timeout=300.0)

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        """Launch an app, answering its pid when simctl says it; `terminate_running` ends a running copy first."""
        for arg in args:
            if not isinstance(arg, str) or "\x00" in arg:
                raise SimctlError(f"not a usable launch argument: {arg!r}")
        fresh = ("--terminate-running-process",) if terminate_running else ()
        result = await self._run("launch", *fresh, _udid(udid), _bundle_id(bundle_id), *args, timeout=60.0)
        match = _PID.search(result.out.strip())
        return int(match.group(1)) if match else None

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        """Quit an app, answering whether one was running: simctl's "found nothing to terminate" is not an error."""
        result = await self._run(
            "terminate", _udid(udid), _bundle_id(bundle_id), tolerate=("found nothing to terminate",)
        )
        return result.ok

    async def openurl(self, udid: str, url: str) -> None:
        await self._run("openurl", _udid(udid), _text(url, "URL"))

    async def pbcopy(self, udid: str, text: str) -> None:
        """Put text on the device's pasteboard -- how any Unicode reaches a field, whatever the keyboard layout."""
        await self._run("pbcopy", _udid(udid), input_data=text.encode("utf-8"))

    async def appearance(self, udid: str, mode: str) -> None:
        if mode not in APPEARANCES:
            raise SimctlError(f"not an appearance: {mode!r}")
        await self._run("ui", _udid(udid), "appearance", mode)

    async def screenshot(self, udid: str, *, kind: str = "jpeg") -> bytes:
        """The screen as an image, written by simctl to its stdout."""
        if kind not in SCREENSHOT_TYPES:
            raise SimctlError(f"not a screenshot type: {kind!r}")
        result = await self._run("io", _udid(udid), "screenshot", f"--type={kind}", "-", timeout=15.0)
        if not result.raw:
            raise SimctlError("simctl io screenshot wrote no image", result)
        return result.raw

    async def log_show(self, udid: str, *, since_s: int, predicate: str) -> str:
        """The device's unified log for the last `since_s` seconds that `predicate` matches, compact."""
        result = await self._run(
            "spawn",
            _udid(udid),
            "log",
            "show",
            "--last",
            f"{int(since_s)}s",
            "--style",
            "compact",
            "--predicate",
            _text(predicate, "log predicate"),
            timeout=30.0,
        )
        return result.out
