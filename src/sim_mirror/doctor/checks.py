# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks, in the order a person fixes things: the Mac, Xcode, its Simulator frameworks and runtimes,
idb_companion, which connector that leaves, the session, and a real tap.

Each check answers one `CheckResult` with a fix a person can follow; nothing here installs, selects or changes
anything. A check that itself fails is reported as failing rather than stopping the doctor, and on anything but a Mac
the rest are skipped.
"""

from __future__ import annotations

import asyncio
import platform
import shutil
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.idb.companion import COMPANION_CANDIDATES, companion_version, find_companion
from sim_mirror.connectors.idb.frameworks import CORE_SIMULATOR, framework_version, simulator_kit, xcode_contents
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.core.runtime import Runtime
from sim_mirror.doctor import macos, tap
from sim_mirror.doctor.report import CheckResult, Report
from sim_mirror.platform import process
from sim_mirror.platform.developer_dir import selected_developer_dir
from sim_mirror.platform.process import Runner
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.platform.xcode import xcode_version
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

INSTALL_XCODE = "Install Xcode from the App Store, then select it: `sudo xcode-select -s /Applications/Xcode.app`."
FIRST_LAUNCH = "Open Xcode once to finish installing its components, or run `sudo xcodebuild -runFirstLaunch`."
INSTALL_RUNTIME = "Add an iOS simulator runtime in Xcode → Settings → Components."
INSTALL_COMPANION = "brew install facebook/fb/idb-companion"


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
    #: The developer folder the Xcode check found, for the checks after it.
    developer_dir: str | None = None


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[DoctorContext], Awaitable[CheckResult]]


async def check_mac(ctx: DoctorContext) -> CheckResult:
    if ctx.platform != "darwin":
        return CheckResult("mac", "fail", "SimMirror runs only on a Mac, where the iOS Simulator runs")
    return CheckResult("mac", "ok", f"macOS {ctx.mac_version() or 'version unknown'}")


async def check_xcode(ctx: DoctorContext) -> CheckResult:
    chosen = ctx.config.developer_dir or await selected_developer_dir(ctx.run)
    if not chosen:
        return CheckResult("xcode", "fail", "no Xcode is selected", INSTALL_XCODE)
    if not Path(chosen).is_dir():
        return CheckResult("xcode", "fail", f"{chosen} does not exist", INSTALL_XCODE)
    if xcode_contents(Path(chosen)) is None:
        return CheckResult(
            "xcode", "fail", f"{chosen} is the command-line tools, which have no Simulator", INSTALL_XCODE
        )
    version = await xcode_version(chosen, ctx.xcrun)
    if version is None:
        return CheckResult("xcode", "fail", f"the xcodebuild in {chosen} did not answer", FIRST_LAUNCH)
    ctx.developer_dir = chosen
    return CheckResult("xcode", "ok", f"{version} at {chosen}")


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
    if binary is None:
        return CheckResult(
            "companion",
            "warn",
            "not installed: simulators are shown through simctl, view-only",
            f"`{INSTALL_COMPANION}` for touch, typing and reading the screen.",
        )
    return CheckResult("companion", "ok", f"{binary} ({await companion_version(binary, ctx.run) or 'version unknown'})")


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
    Check("companion", check_companion),
    Check("connectors", check_connectors),
    Check("device hub", _device_hub),
    Check("desktop session", _gui_session),
    Check("accessibility", _accessibility),
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
