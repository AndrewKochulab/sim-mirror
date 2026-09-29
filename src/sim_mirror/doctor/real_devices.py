# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks for real devices and recording: what a person can see is set up, and what to do if it is not.

* **real devices** -- whether devicectl lists the iPhones and iPads connected to this Mac, and what stands in each
  one's way (pairing, Developer Mode);
* **cable screen** -- whether the native helper can read a cabled device's screen (`connectors.iphone.capture`);
* **webdriveragent** -- whether it is on, and built for the team and Xcode in the settings (`sim-mirror wda`);
* **recording** -- whether recordings have a folder to go in and a helper to render them.

None of them changes anything on a device, or starts anything that would.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from sim_mirror.build.wda import xctestrun
from sim_mirror.connectors.iphone.capture import FEATURE as CAPTURE
from sim_mirror.connectors.iphone.wda import derived_for, wda_root
from sim_mirror.connectors.native.helper import HelperVersion, helper_able, helper_version
from sim_mirror.core.backends import runtime_of
from sim_mirror.core.recordings import default_folder
from sim_mirror.core.render import FEATURE as RENDER
from sim_mirror.doctor.report import CheckResult
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError
from sim_mirror.storage.private import ensure_private_dir

if TYPE_CHECKING:
    from sim_mirror.doctor.checks import DoctorContext

TURN_ON = "`sim-mirror config set real_devices.enabled true`"


def _xcode(ctx: DoctorContext) -> str:
    return ctx.config.real_devices_developer_dir or ctx.config.developer_dir


async def _helper_can(ctx: DoctorContext, feature: str, doing: str) -> tuple[str | None, str | None]:
    async def version_of(path: str) -> HelperVersion | None:
        return await helper_version(path, ctx.run)

    configured = ctx.config.native_helper_path
    return await helper_able(feature, doing, configured, ctx.helper_candidates(), version_of, HostCopy())


async def check_real_devices(ctx: DoctorContext) -> CheckResult:
    name = "real devices"
    if not ctx.config.real_devices:
        return CheckResult(name, "ok", f"off; {TURN_ON} lists the iPhones and iPads connected to this Mac")
    try:
        found = await Devicectl(ctx.xcrun, developer_dir=_xcode(ctx)).devices()
    except DevicectlError as exc:
        return CheckResult(
            name,
            "warn",
            f"the devices connected to this Mac cannot be listed: {exc}",
            "Open Xcode once, then try again.",
        )
    connected = [device for device in found if device.connected]
    if not connected:
        return CheckResult(name, "ok", "none connected; plug one in by cable, unlock it and choose Trust")
    said = "; ".join(
        f"{device.name}, {runtime_of(device)} ({device.connection}{', ' + device.detail if device.detail else ''})"
        for device in connected
    )
    blocked = [device for device in connected if device.detail]
    if blocked:
        return CheckResult(name, "warn", said, "Unlock each device, choose Trust, and turn on Developer Mode.")
    return CheckResult(name, "ok", said)


async def check_cable_screen(ctx: DoctorContext) -> CheckResult:
    name = "cable screen"
    if not ctx.config.real_devices:
        return CheckResult(name, "ok", "off with real devices")
    if ctx.config.real_devices_screen not in ("auto", "usb"):
        return CheckResult(name, "ok", f"not used (real_devices.screen is {ctx.config.real_devices_screen})")
    binary, why = await _helper_can(ctx, CAPTURE, "show a cabled device's screen")
    if binary is None:
        return CheckResult(name, "warn", why or "no native helper", "A cabled device's screen is shown by screenshots.")
    return CheckResult(name, "ok", f"{binary} reads it; macOS asks once to let sim-mirror-helper use the Camera")


async def check_webdriveragent(ctx: DoctorContext) -> CheckResult:
    name = "webdriveragent"
    config = ctx.config
    if not (config.real_devices and config.wda_enabled):
        return CheckResult(name, "ok", "off; a real device is watched, not touched (real_devices.wda.enabled)")
    team = config.real_devices_team_id
    if not team:
        return CheckResult(name, "warn", "no team to sign it with", "Set real_devices.team_id: `sim-mirror wda teams`.")
    built = xctestrun(derived_for(wda_root(ctx.env), team, _xcode(ctx)))
    if built is None:
        return CheckResult(name, "warn", f"not built for team {team}", "`sim-mirror wda setup --device <udid>`.")
    return CheckResult(name, "ok", f"built for team {team}: {built.name}")


async def check_recording(ctx: DoctorContext) -> CheckResult:
    name = "recording"
    folder = default_folder(ctx.config)
    if not _writable(folder):
        return CheckResult(name, "fail", f"{folder} cannot be written", "Set recording.folder to a folder you own.")
    binary, why = await _helper_can(ctx, RENDER, "render recordings")
    if binary is None:
        return CheckResult(name, "warn", f"kept in {folder}; {why}", "A simulator's recording is kept as it was.")
    return CheckResult(name, "ok", f"kept in {folder}, rendered by {binary}")


def _writable(folder: Path) -> bool:
    try:
        ensure_private_dir(folder)
    except OSError:
        return False
    return os.access(folder, os.W_OK)
