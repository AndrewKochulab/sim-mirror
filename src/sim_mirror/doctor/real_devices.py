# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks for real devices and recording: what a person can see is set up, and what to do if it is not.

* **real devices** -- whether devicectl lists the iPhones and iPads connected to this Mac, and what stands in each
  one's way (pairing, Developer Mode);
* **cable screen** -- whether the native helper can read a cabled device's screen (`connectors.iphone.capture`);
* **camera** -- whether macOS lets that helper use the Camera, which reading a screen by cable needs, as the helper's
  own log says of its latest run for each device: it read a screen, or macOS withheld the Camera, or what else it said
  went wrong. Asking macOS itself would show its permission prompt, so it is not asked;
* **webdriveragent** -- whether it is on, whether this Mac can sign it, and whether it is set up (`sim-mirror wda`);
* **touch now** -- whether each real device a running daemon drives, for any folder's project, is touched now, and
  why not -- set up is not running, and a device whose cable came out is watched, not touched -- with what last kept
  WebDriverAgent from driving it;
* **recording** -- whether recordings have a folder to go in and a helper to render them.

None of them changes anything on a device, or starts anything that would.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sim_mirror.build.wda import xctestrun
from sim_mirror.connectors.helper_process import ERROR_MARK, ERROR_TAIL_BYTES
from sim_mirror.connectors.iphone.capture import FEATURE as CAPTURE
from sim_mirror.connectors.iphone.wda_setup import derived_for, wda_root
from sim_mirror.connectors.native.helper import FOLDER as NATIVE_LOG_PREFIX
from sim_mirror.connectors.native.helper import HelperVersion, helper_able, helper_version
from sim_mirror.core.backends import runtime_of
from sim_mirror.core.recordings import default_folder
from sim_mirror.core.render import FEATURE as RENDER
from sim_mirror.core.signing import SigningTeams
from sim_mirror.doctor.report import CheckResult
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError
from sim_mirror.platform.keychain import development_teams
from sim_mirror.storage.private import ensure_private_dir

if TYPE_CHECKING:
    from sim_mirror.doctor.checks import DoctorContext

TURN_ON = "`sim-mirror config set real_devices.enabled true`"
TOUCH_NOW = "touch now"
CAMERA = "camera"
#: What a cabled device's screen helper writes when it starts, and once it reads the screen: a run of it is what its log
#: says from the first of these on.
CAPTURE_STARTED = "serving the screen of"
CAPTURE_READING = "reading the screen of"
#: What lets the helper read a cabled device's screen once macOS withheld the Camera.
CAMERA_FIX = (
    "In System Settings > Privacy & Security > Camera, allow sim-mirror-helper -- or the app that started SimMirror, "
    "which macOS may ask instead -- then unplug the device and plug it back in."
)
#: What gets a real device that is not touched now touched again.
TOUCH_FIX = (
    "Plug it into the Mac by cable -- not through a hub -- and unlock it: it is attached again by itself within "
    "seconds. `sim_device restart`, or picking it again in the viewer, starts its session over at once."
)


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


@dataclass(frozen=True)
class CaptureRun:
    """What a cabled device's screen helper said in its latest run: whether it read the screen, and its last error."""

    log: Path
    reading: bool
    error: str | None


def capture_runs(log_dir: Path) -> list[CaptureRun]:
    """The latest run of each cabled device's screen helper, as its log says, the most recently written first. A log
    with no such run -- a simulator's helper's -- is left out."""
    logs = sorted(log_dir.glob(f"{NATIVE_LOG_PREFIX}-*.log"), key=_written, reverse=True)
    found: list[CaptureRun] = []
    for log in logs:
        lines = _tail_lines(log)
        starts = [index for index, line in enumerate(lines) if CAPTURE_STARTED in line]
        if not starts:
            continue
        run = lines[starts[-1] :]
        errors = [line.removeprefix(ERROR_MARK).strip() for line in run if line.startswith(ERROR_MARK)]
        found.append(CaptureRun(log, any(CAPTURE_READING in line for line in run), errors[-1] if errors else None))
    return found


def _written(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _tail_lines(log: Path) -> list[str]:
    try:
        with log.open("rb") as file:
            file.seek(max(0, file.seek(0, os.SEEK_END) - ERROR_TAIL_BYTES))
            said = file.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in said.splitlines() if line.strip()]


async def check_camera(ctx: DoctorContext) -> CheckResult:
    if not ctx.config.real_devices or ctx.config.real_devices_screen not in ("auto", "usb"):
        return CheckResult(CAMERA, "ok", "not used: no cabled device's screen is read here")
    if ctx.log_dir is None:
        return CheckResult(CAMERA, "skip", "not asked")
    runs = await asyncio.to_thread(capture_runs, ctx.log_dir)
    withheld = next((run for run in runs if run.error and "Camera" in run.error and not run.reading), None)
    if withheld is not None:
        return CheckResult(CAMERA, "warn", f"the cable screen's helper said: {withheld.error}", CAMERA_FIX)
    failed = next((run for run in runs if run.error), None)
    if failed is not None:
        return CheckResult(
            CAMERA,
            "warn",
            f"the cable screen's helper said: {failed.error} (its log is {failed.log})",
            "Unlock the device and plug it straight into the Mac; it is attached again by itself.",
        )
    if any(run.reading for run in runs):
        return CheckResult(CAMERA, "ok", "macOS lets sim-mirror-helper use the Camera: it read a cabled device")
    return CheckResult(
        CAMERA, "ok", "no cabled device's screen has been read yet; macOS asks for the Camera the first time"
    )


async def check_webdriveragent(ctx: DoctorContext) -> CheckResult:
    name = "webdriveragent"
    config = ctx.config
    if not (config.real_devices and config.wda_enabled):
        return CheckResult(name, "ok", "off; a real device is watched, not touched (real_devices.wda.enabled)")
    teams = SigningTeams(lambda _: None, lambda: development_teams(ctx.run))
    if not await teams.on_mac() and not config.real_devices_team_id:
        return CheckResult(
            name,
            "ok",
            "no development certificate on this Mac, so a real device is watched, not touched: sign in to Xcode > "
            "Settings > Accounts to touch one",
        )
    found = await teams.outside_project(config)
    if found is None:
        return CheckResult(name, "ok", "signed by each project's own team; outside a project, set real_devices.team_id")
    built = xctestrun(derived_for(wda_root(ctx.env), found.team, _xcode(ctx)))
    if built is None:
        return CheckResult(
            name,
            "ok",
            f"not set up yet for team {found.team}",
            "Set up touch in the viewer, or `sim-mirror wda setup --device <udid>`, sets it up once.",
        )
    return CheckResult(name, "ok", f"set up for team {found.team}: {built.name}")


async def check_touch_now(ctx: DoctorContext) -> CheckResult:
    if ctx.touch_now is None:
        return CheckResult(TOUCH_NOW, "skip", "not asked")
    devices, none = await asyncio.to_thread(ctx.touch_now)
    if not devices:
        return CheckResult(TOUCH_NOW, "ok", none)
    said = [_touch_words(device) for device in devices]
    untouched = any(not fine for fine, _ in said)
    return CheckResult(
        TOUCH_NOW, "warn" if untouched else "ok", "; ".join(words for _, words in said), TOUCH_FIX if untouched else ""
    )


def _touch_words(device: dict[str, Any]) -> tuple[bool, str]:
    """Whether touching a device is as it should be -- touched, or on its way to it, or off -- and what to say of it."""
    name, touch, said = device["name"], device["touch"], device["message"]
    last = device.get("last_error")
    if touch == "ready":
        return True, f"WebDriverAgent touches {name} now"
    if touch in ("off", "starting", "building"):
        return True, f"{name}: {said or touch}"
    since = f" (last error: {last})" if last and last != said else ""
    return False, f"{name} is not touched now: {said or touch}{since}"


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
