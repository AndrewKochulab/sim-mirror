# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks, in the order a person fixes things: the Mac, Xcode, its Simulator frameworks and runtimes,
the native helper, idb_companion and the companions already running, which connector that leaves, the session,
Xcode 27's UI hierarchy where a scope reads it, reading text in a screen's pixels, and a real tap.

Each check answers one `CheckResult` with a fix a person can follow; nothing here installs, selects or changes
anything. A check that itself fails is reported as failing rather than stopping the doctor, and on anything but a Mac
the rest are skipped.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError
from sim_mirror.connectors.idb.companion import (
    COMPANION_CANDIDATES,
    companion_version,
    find_companion,
    recorded_companions,
    runs_companion,
)
from sim_mirror.connectors.idb.frameworks import CORE_SIMULATOR, framework_version, simulator_kit, xcode_contents
from sim_mirror.connectors.mcpbridge import connector as mcpbridge
from sim_mirror.connectors.mcpbridge.client import find_bridge
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.connectors.native import connector as native
from sim_mirror.connectors.native.helper import HelperVersion, SelfCheck, helper_version, locate_helper, self_check
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.core.runtime import Runtime
from sim_mirror.doctor import macos, tap
from sim_mirror.doctor.report import CheckResult, Report
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.ocr import RecognitionOptions, TextRecognitionError
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.vision.helper import VisionHelpers
from sim_mirror.perception.vision.probe import PROBE_TEXT, probe_picture
from sim_mirror.platform import process
from sim_mirror.platform.developer_dir import ChosenXcode, choose_xcode, selected_developer_dir
from sim_mirror.platform.process import Runner
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.platform.xcode import xcode_version
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.storage.app_support import helpers_dir

INSTALL_XCODE = "Install Xcode from the App Store, then select it: `sudo xcode-select -s /Applications/Xcode.app`."
FIRST_LAUNCH = "Open Xcode once to finish installing its components, or run `sudo xcodebuild -runFirstLaunch`."
INSTALL_RUNTIME = "Add an iOS simulator runtime in Xcode → Settings → Components."
INSTALL_COMPANION = "brew install facebook/fb/idb-companion"
BUILD_HELPER = "sim-mirror helper build"
READING_OFF = "`sim-mirror config set perception.ocr off` stops reading pixels; snapshots then read accessibility only."


def vision_for(ctx: DoctorContext) -> VisionHelpers:
    """SimMirror's text reader, compiled where a daemon with this environment keeps it."""
    return VisionHelpers(folder=helpers_dir(ctx.env), xcrun=ctx.xcrun)


@dataclass
class DoctorContext:
    config: SimConfig
    registry: ConnectorRegistry
    #: A runtime to tap with; None skips the tap.
    runtime: Runtime | None = None
    device: str | None = None
    run: Runner = process.run
    xcrun: XcrunRunner = run_xcrun
    platform: str = sys.platform
    mac_version: Callable[[], str] = field(default=lambda: platform.mac_ver()[0])
    which: Callable[[str], str | None] = shutil.which
    companion_candidates: Sequence[str] = COMPANION_CANDIDATES
    core_simulator: Path = CORE_SIMULATOR
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    #: The environment SimMirror runs with, whose DEVELOPER_DIR every program it starts inherits.
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    #: Where this host's companions keep their pid files; None skips looking at the running ones.
    run_dir: Path | None = None
    pid_alive: Callable[[int], bool] = process.pid_alive
    command_of: Callable[[int], Awaitable[str | None]] = process.command_of
    #: The Xcode the Xcode check found, for the checks after it.
    xcode: ChosenXcode | None = None
    #: How Xcode's UI hierarchy of a device is read, given its UDID and the Xcode.
    hierarchy_reader: Callable[[str, str], BridgeReader] = BridgeReader
    #: The text reader the screen reading check reads a picture with.
    vision: Callable[[DoctorContext], VisionHelpers] = vision_for
    clock: Callable[[], float] = time.monotonic
    #: Where a native helper is looked for when none is configured.
    helper_candidates: Callable[[], Sequence[Path]] = native.default_candidates
    #: How a native helper checks a device, given the helper, the device's UDID and the Xcode.
    helper_self_check: Callable[[str, str, str], Awaitable[SelfCheck | None]] = self_check
    #: Whether the native helper check found a helper this SimMirror can use, for the checks after it.
    native_ready: bool = False

    @property
    def developer_dir(self) -> str | None:
        return None if self.xcode is None else self.xcode.path


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[DoctorContext], Awaitable[CheckResult]]


async def check_mac(ctx: DoctorContext) -> CheckResult:
    if ctx.platform != "darwin":
        return CheckResult("mac", "fail", "SimMirror runs only on a Mac, where the iOS Simulator runs")
    return CheckResult("mac", "ok", f"macOS {ctx.mac_version() or 'version unknown'}")


async def check_xcode(ctx: DoctorContext) -> CheckResult:
    """The Xcode SimMirror's programs run with, saying what named it -- and, when that is not xcode-select, which
    Xcode the rest of the Mac uses, since a person looking at Xcode's own window sees that one."""
    chosen = await choose_xcode(ctx.config.developer_dir, ctx.env, ctx.run)
    if chosen is None:
        return CheckResult("xcode", "fail", "no Xcode is selected", INSTALL_XCODE)
    if not Path(chosen.path).is_dir():
        return CheckResult("xcode", "fail", f"{chosen} does not exist", INSTALL_XCODE)
    if xcode_contents(Path(chosen.path)) is None:
        return CheckResult(
            "xcode", "fail", f"{chosen} is the command-line tools, which have no Simulator", INSTALL_XCODE
        )
    version = await xcode_version(chosen.path, ctx.xcrun)
    if version is None:
        return CheckResult("xcode", "fail", f"the xcodebuild in {chosen} did not answer", FIRST_LAUNCH)
    ctx.xcode = chosen
    detail = f"{version} at {chosen}"
    if chosen.source != "xcode-select":
        selected = await selected_developer_dir(ctx.run)
        if selected and selected != chosen.path:
            detail += f"; the rest of this Mac uses {selected} (xcode-select)"
    return CheckResult("xcode", "ok", detail)


def _needs_xcode(name: str) -> CheckResult:
    return CheckResult(name, "skip", "needs a working Xcode")


async def check_frameworks(ctx: DoctorContext) -> CheckResult:
    if ctx.developer_dir is None:
        return _needs_xcode("simulator frameworks")
    kit = simulator_kit(Path(ctx.developer_dir))
    core = framework_version(ctx.core_simulator) or "version unknown"
    if kit is None:
        return CheckResult(
            "simulator frameworks",
            "warn",
            f"SimulatorKit.framework is not in this Xcode (CoreSimulator {core})",
            "Reinstall the iOS platform in Xcode → Settings → Components.",
        )
    place = "SharedFrameworks" if kit.parent.name == "SharedFrameworks" else "PrivateFrameworks"
    kit_version = framework_version(kit) or "version unknown"
    return CheckResult("simulator frameworks", "ok", f"SimulatorKit {kit_version} in {place}; CoreSimulator {core}")


async def check_runtimes(ctx: DoctorContext) -> CheckResult:
    if ctx.developer_dir is None:
        return _needs_xcode("runtimes")
    try:
        runtimes = await Simctl(ctx.xcrun, developer_dir=ctx.developer_dir).runtimes()
    except SimctlError as exc:
        return CheckResult("runtimes", "fail", str(exc), FIRST_LAUNCH)
    ios = [runtime.name for runtime in runtimes if runtime.available and runtime.platform == "iOS"]
    if not ios:
        return CheckResult("runtimes", "fail", "no iOS simulator runtime is installed", INSTALL_RUNTIME)
    return CheckResult("runtimes", "ok", ", ".join(ios))


async def check_native_helper(ctx: DoctorContext) -> CheckResult:
    """Whether a native helper of this SimMirror's version is there -- and, with a booted simulator and a tap allowed,
    whether it reaches that device: its screen, a picture, input and the element tree."""
    name = "native helper"
    config = ctx.config
    used = config.connector in ("auto", native.NAME)
    configured = config.native_helper_path

    async def version_of(path: str) -> HelperVersion | None:
        return await helper_version(path, ctx.run)

    located = await locate_helper(configured, ctx.helper_candidates(), version_of)
    binary, version = located.binary, located.version
    if binary is None:
        if not used:
            return CheckResult(name, "ok", f"not built; not used (connectors.preferred is {config.connector})")
        if configured:
            return CheckResult(
                name,
                "fail",
                f"the helper at {configured} cannot be run",
                f"Build one with `{BUILD_HELPER}`, or unset connectors.native.helper_path.",
            )
        return CheckResult(name, "warn", "not built for this install", f"`{BUILD_HELPER}` (it needs Xcode).")
    if version is None or not version.usable:
        said = "does not say its version" if version is None else f"is version {version.version} (wire {version.wire})"
        return CheckResult(name, "warn" if used else "ok", f"{binary} {said}", f"Build it again with `{BUILD_HELPER}`.")
    ctx.native_ready = True
    found = f"{binary} ({version.version}, CoreSimulator {version.core_simulator or 'unknown'})"
    if ctx.runtime is None or ctx.developer_dir is None:
        return CheckResult(name, "ok", found)
    udid = await _booted(ctx, ctx.developer_dir)
    if udid is None:
        return CheckResult(name, "ok", f"{found}; no booted simulator to check it with")
    checked = await ctx.helper_self_check(binary, udid, ctx.developer_dir)
    ctx.native_ready = checked is not None and checked.ok
    if checked is None:
        return CheckResult(name, "fail", f"{found}; it said nothing readable when it checked {udid}")
    parts = "; ".join(f"{part.name}: {part.detail}" for part in checked.parts)
    if checked.ok:
        return CheckResult(name, "ok", f"{found}; reached {udid}: {parts}")
    failed = [part.name for part in checked.parts if not part.ok]
    fix = (
        "Try the other input path with `sim-mirror config set connectors.native.hid_transport indigo` (or dtuhid)."
        if "input" in failed
        else "Unlock the simulator and let it finish starting, then run the doctor again."
    )
    return CheckResult(name, "fail", f"{found}; could not reach {udid}: {parts}", fix)


async def check_companion(ctx: DoctorContext) -> CheckResult:
    configured = ctx.config.companion_path
    binary = find_companion(configured, ctx.companion_candidates, ctx.which)
    if binary is None and configured:
        return CheckResult(
            "companion",
            "fail",
            f"the companion at {configured} cannot be run",
            f"Install it with `{INSTALL_COMPANION}`, or unset connectors.idb.companion_path.",
        )
    if binary is None and ctx.native_ready:
        return CheckResult("companion", "ok", "not installed; not needed while the native helper drives simulators")
    if binary is None:
        return CheckResult(
            "companion",
            "warn",
            "not installed, and there is no native helper: simulators are shown through simctl, view-only",
            f"`{BUILD_HELPER}`, or `{INSTALL_COMPANION}`, for touch, typing and reading the screen.",
        )
    found = f"{binary} ({await companion_version(binary, ctx.run) or 'version unknown'})"
    started_with = f"; it starts with {ctx.developer_dir}" if ctx.developer_dir else ""
    return CheckResult("companion", "ok", found + started_with)


async def check_running_companions(ctx: DoctorContext) -> CheckResult:
    """Which Xcode each companion already running runs with. A companion keeps the Xcode it started with, so one
    started before the Mac's selection or a scope's setting changed is still on the old one."""
    if ctx.run_dir is None:
        return CheckResult("running companions", "skip", "not checked here")
    running = [
        record
        for record in recorded_companions(ctx.run_dir)
        if await runs_companion(record.pid, ctx.pid_alive, ctx.command_of)
    ]
    if not running:
        return CheckResult("running companions", "ok", "none")
    each = "; ".join(
        f"pid {record.pid} with {record.developer_dir or 'an Xcode it did not record'}" for record in running
    )
    return CheckResult("running companions", "ok", each)


async def check_connectors(ctx: DoctorContext) -> CheckResult:
    reports = await ctx.registry.reports(ctx.config)
    found = "; ".join(
        f"{report.name}: {'available' if report.available else ', '.join(report.reasons) or 'unavailable'}"
        for report in reports
    )
    selection = await ctx.registry.select(ctx.config)
    if selection.connector is None:
        return CheckResult("connectors", "fail", f"none can be used ({found})", selection.refusal or "")
    chosen = f"{selection.connector.name} is used ({found})"
    if selection.fallback_reason:
        return CheckResult("connectors", "warn", f"{chosen}: {selection.fallback_reason}")
    return CheckResult("connectors", "ok", chosen)


async def _booted(ctx: DoctorContext, developer_dir: str) -> str | None:
    """The simulator to read: the one ``--device`` names, else the first booted one -- only while it is booted, since
    Xcode would boot it and the read would wait out its boot."""
    try:
        booted = [
            device.udid for device in await Simctl(ctx.xcrun, developer_dir=developer_dir).devices() if device.booted
        ]
    except SimctlError:
        return None
    if ctx.device:
        return ctx.device if ctx.device in booted else None
    return booted[0] if booted else None


async def check_xcode_tools(ctx: DoctorContext) -> CheckResult:
    """Whether the Xcode in use has mcpbridge -- and, when a scope reads the screen through it, a real read of a booted
    device's hierarchy, which is also where Xcode says it has not approved SimMirror."""
    name = "xcode tools"
    config = ctx.config
    chosen = config.connector == mcpbridge.NAME
    used = chosen or config.mcpbridge_merge
    how = "connectors.preferred is mcpbridge" if chosen else "connectors.mcpbridge.merge is on"
    if ctx.developer_dir is None:
        return _needs_xcode(name)
    copy = HostCopy()
    bridge = await find_bridge(ctx.developer_dir, ctx.xcrun)
    if bridge is None:
        if used:
            fix = "Choose Xcode 27 with `sim-mirror config set device.developer_dir`, or stop reading through it."
            return CheckResult(name, "fail", f"{how}, but {copy.mcpbridge_missing(ctx.developer_dir)}", fix)
        return CheckResult(name, "ok", "this Xcode has no mcpbridge, which reading the screen through Xcode needs")
    if not used:
        return CheckResult(name, "ok", f"mcpbridge at {bridge}; not used")
    udid = await _booted(ctx, ctx.developer_dir) if ctx.runtime is not None else None
    if udid is None:
        return CheckResult(name, "skip", f"mcpbridge at {bridge}; {how}, and reading needs a booted simulator")
    reader = ctx.hierarchy_reader(udid, ctx.developer_dir)
    started = ctx.clock()
    try:
        elements = sum(1 for _ in tree_from_document(await reader.accessibility()).walk())
    except ConnectorError as exc:
        return CheckResult(name, "fail", f"{how}, but Xcode's hierarchy of {udid} could not be read: {exc}")
    finally:
        await reader.close()
    took = ctx.clock() - started
    return CheckResult(name, "ok", f"read {elements} elements of {udid} through {bridge} in {took:.1f}s")


async def check_screen_reading(ctx: DoctorContext) -> CheckResult:
    """Whether a screen can be read from its pixels: SimMirror's text reader compiled with the Xcode in use, and a
    reading of a picture whose text is known."""
    name = "screen reading"
    mode = ctx.config.ocr_mode
    if mode == "off":
        return CheckResult(name, "ok", "perception.ocr is off: no screen is read from its pixels")
    if ctx.developer_dir is None:
        return _needs_xcode(name)
    vision = ctx.vision(ctx)
    options = replace(RecognitionOptions.from_config(ctx.config), developer_dir=ctx.developer_dir)
    started = ctx.clock()
    try:
        lines = await vision.recognize(probe_picture(), options)
        languages = await vision.languages(ctx.developer_dir)
    except TextRecognitionError as exc:
        return CheckResult(name, "warn", f"perception.ocr is {mode}, but {exc}", READING_OFF)
    finally:
        await vision.close()
    took = ctx.clock() - started
    said = " ".join(line.text for line in lines)
    if "".join(PROBE_TEXT.split()).casefold() not in "".join(said.split()).casefold():
        return CheckResult(
            name, "warn", f"the text reader read {said or 'nothing'!r} in a picture of {PROBE_TEXT!r}", READING_OFF
        )
    return CheckResult(
        name, "ok", f"read a test picture in {took:.1f}s with Vision, in {len(languages)} languages ({mode})"
    )


async def _device_hub(ctx: DoctorContext) -> CheckResult:
    return await macos.check_device_hub(ctx.run)


async def _gui_session(ctx: DoctorContext) -> CheckResult:
    return await macos.check_gui_session(ctx.run)


async def _accessibility(ctx: DoctorContext) -> CheckResult:
    return macos.check_accessibility()


async def _tap(ctx: DoctorContext) -> CheckResult:
    return await tap.check_tap(ctx.runtime, ctx.device, ctx.sleep)


CHECKS: tuple[Check, ...] = (
    Check("mac", check_mac),
    Check("xcode", check_xcode),
    Check("simulator frameworks", check_frameworks),
    Check("runtimes", check_runtimes),
    Check("native helper", check_native_helper),
    Check("companion", check_companion),
    Check("running companions", check_running_companions),
    Check("connectors", check_connectors),
    Check("device hub", _device_hub),
    Check("desktop session", _gui_session),
    Check("accessibility", _accessibility),
    Check("xcode tools", check_xcode_tools),
    Check("screen reading", check_screen_reading),
    Check(tap.NAME, _tap),
)


async def diagnose(ctx: DoctorContext, checks: Sequence[Check] = CHECKS) -> Report:
    """Run every check in order. A check that raises is reported as failing; off a Mac, the rest are skipped."""
    results: list[CheckResult] = []
    for check in checks:
        if ctx.platform != "darwin" and results:
            results.append(CheckResult(check.name, "skip", "needs a Mac"))
            continue
        try:
            results.append(await check.run(ctx))
        except Exception as exc:
            results.append(CheckResult(check.name, "fail", f"the check itself failed: {exc}"))
    return Report(tuple(results))
