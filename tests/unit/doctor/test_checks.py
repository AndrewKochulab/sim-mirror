# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks on a fake Mac: all well, then each thing a person can get wrong, and what it tells them to do."""

from __future__ import annotations

import dataclasses
import plistlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.connectors.mcpbridge.client import BridgeClient
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.doctor.checks import (
    CHECKS,
    FIRST_LAUNCH,
    INSTALL_RUNTIME,
    INSTALL_XCODE,
    Check,
    DoctorContext,
    check_screen_reading,
    check_xcode_tools,
    diagnose,
    vision_for,
)
from sim_mirror.doctor.report import CheckResult
from sim_mirror.perception.vision.helper import VisionHelpers
from sim_mirror.perception.vision.probe import PROBE_TEXT
from sim_mirror.platform.developer_dir import ChosenXcode
from sim_mirror.testing.fakes import BOOTED_UDID, FakeBridge, FakeConnector, FakeXcrun, ManualClock
from sim_mirror.testing.rig import VIEW_ONLY
from sim_mirror.testing.vision import FakeVisionHelper

VERSION = '{"build_date": "Sep 15 2026", "build_time": "10:00:00"}'


def plist(folder: Path, short: str, build: str) -> None:
    (folder / "Resources").mkdir(parents=True)
    with (folder / "Resources" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": short, "CFBundleVersion": build}, handle)


class Mac:
    """A Mac with Xcode 26.6 selected, its frameworks, runtimes and idb_companion -- each part a test can take away."""

    def __init__(self, root: Path) -> None:
        self.developer = root / "Xcode.app" / "Contents" / "Developer"
        self.developer.mkdir(parents=True)
        plist(self.developer.parent / "SharedFrameworks" / "SimulatorKit.framework", "946.1", "946.1.2")
        self.core = root / "CoreSimulator.framework"
        plist(self.core, "1051.9", "1051.9.4")
        self.companion = root / "bin" / "idb_companion"
        self.companion.parent.mkdir()
        self.companion.write_text("#!/bin/sh\n")
        self.companion.chmod(0o755)
        self.answers: dict[tuple[str, ...], tuple[int, str]] = {
            ("xcode-select", "-p"): (0, f"{self.developer}\n"),
            ("launchctl", "managername"): (0, "Aqua\n"),
            (str(self.companion), "--version"): (0, VERSION),
        }
        self.xcrun = FakeXcrun().with_lists().on("xcodebuild", "-version", out="Xcode 26.6\nBuild version 17F42\n")
        self.xcrun.with_swift()
        self.installed: str | None = str(self.companion)
        self.helpers = root / "helpers"
        #: SimMirror's text reader, reading the doctor's test picture as Vision did.
        self.reader = FakeVisionHelper([{"text": PROBE_TEXT, "confidence": 1, "box": {"x": 0.1, "y": 0.3, "w": 0.8,
                                                                                      "h": 0.4}}])  # fmt: skip

    def vision(self, ctx: DoctorContext) -> VisionHelpers:
        return VisionHelpers(folder=self.helpers, xcrun=ctx.xcrun, spawn=self.reader.spawn)

    async def run(self, argv: Sequence[str]) -> tuple[int, str]:
        return self.answers.get(tuple(argv), (1, ""))

    def context(self, **changes: Any) -> DoctorContext:
        registry = ConnectorRegistry([FakeConnector("idb"), FakeConnector("simctl", capabilities=VIEW_ONLY)])
        ctx = DoctorContext(
            config=SimConfig.defaults(),
            registry=registry,
            run=self.run,
            xcrun=self.xcrun,
            platform="darwin",
            mac_version=lambda: "26.6.2",
            which=lambda program: self.installed,
            companion_candidates=(),
            core_simulator=self.core,
            env={},
            vision=self.vision,
        )
        return dataclasses.replace(ctx, **changes)


def by_name(results: Sequence[CheckResult]) -> dict[str, CheckResult]:
    return {result.name: result for result in results}


async def test_a_mac_with_everything_in_place_passes_and_says_what_it_found(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    report = await diagnose(mac.context())
    found = by_name(report.results)
    assert [result.name for result in report.results] == [check.name for check in CHECKS]
    assert found["mac"].detail == "macOS 26.6.2"
    assert found["xcode"].detail == f"Xcode 26.6 (17F42) at {mac.developer} (xcode-select)"
    assert (
        found["simulator frameworks"].detail
        == "SimulatorKit 946.1 (946.1.2) in SharedFrameworks; CoreSimulator 1051.9 (1051.9.4)"
    )
    assert found["runtimes"].detail == "iOS 18.6, iOS 26.5"
    assert found["companion"].detail == f"{mac.companion} (Sep 15 2026 10:00:00); it starts with {mac.developer}"
    assert found["running companions"] == CheckResult("running companions", "skip", "not checked here")
    assert found["connectors"].detail == "idb is used (idb: available; simctl: available)"
    assert found["device hub"].status == found["desktop session"].status == "ok"
    assert found["screen reading"].detail == "read a test picture in 0.0s with Vision, in 11 languages (fallback)"
    assert (found["accessibility"].status, found["test tap"].detail) == ("skip", "skipped (--no-tap)")
    assert (report.status, report.exit_code) == ("ok", 0)


async def test_off_a_mac_nothing_else_is_checked(tmp_path: Path) -> None:
    report = await diagnose(Mac(tmp_path).context(platform="linux"))
    assert report.results[0] == CheckResult("mac", "fail", "SimMirror runs only on a Mac, where the iOS Simulator runs")
    assert {result.status for result in report.results[1:]} == {"skip"} and report.exit_code == 1
    assert (await diagnose(Mac(tmp_path / "unknown").context(mac_version=lambda: ""))).results[0].detail == (
        "macOS version unknown"
    )


async def test_an_xcode_that_is_missing_the_tools_only_or_unfinished_fails_and_skips_what_needs_it(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    tools = tmp_path / "CommandLineTools"
    tools.mkdir()
    cases: list[tuple[DoctorContext, str, str]] = [
        (mac.context(run=lambda argv: _answer(1, "")), "no Xcode is selected", INSTALL_XCODE),
        (mac.context(config=SimConfig.defaults().with_values(developer_dir="/Applications/Gone.app/Contents/Developer")),
         "/Applications/Gone.app/Contents/Developer (device.developer_dir) does not exist", INSTALL_XCODE),
        (mac.context(config=SimConfig.defaults().with_values(developer_dir=str(tools))),
         f"{tools} (device.developer_dir) is the command-line tools, which have no Simulator", INSTALL_XCODE),
        (mac.context(xcrun=FakeXcrun().on("xcodebuild", "-version", rc=1)),
         f"the xcodebuild in {mac.developer} (xcode-select) did not answer", FIRST_LAUNCH),
    ]  # fmt: skip
    for ctx, detail, fix in cases:
        found = by_name((await diagnose(ctx)).results)
        assert (found["xcode"].status, found["xcode"].detail, found["xcode"].fix) == ("fail", detail, fix)
        assert found["simulator frameworks"].detail == found["runtimes"].detail == "needs a working Xcode"


def second_xcode(root: Path) -> Path:
    """Xcode 27 beside the selected Xcode 26.6, as a Mac with both has it."""
    developer = root / "Xcode27.app" / "Contents" / "Developer"
    developer.mkdir(parents=True)
    return developer


async def test_an_inherited_developer_dir_is_the_xcode_reported_and_the_macs_own_selection_is_named(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    xcode_27 = second_xcode(tmp_path)
    found = by_name((await diagnose(mac.context(env={"DEVELOPER_DIR": str(xcode_27)}))).results)
    assert found["xcode"].detail == (
        f"Xcode 26.6 (17F42) at {xcode_27} (DEVELOPER_DIR); the rest of this Mac uses {mac.developer} (xcode-select)"
    )
    assert found["companion"].detail.endswith(f"; it starts with {xcode_27}")
    assert mac.xcrun.calls[0].developer_dir == str(xcode_27)


async def test_a_setting_that_names_the_selected_xcode_says_nothing_about_the_rest_of_the_mac(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    same = SimConfig.defaults().with_values(developer_dir=str(mac.developer))
    found = by_name((await diagnose(mac.context(config=same))).results)
    assert found["xcode"].detail == f"Xcode 26.6 (17F42) at {mac.developer} (device.developer_dir)"
    del mac.answers[("xcode-select", "-p")]
    other = SimConfig.defaults().with_values(developer_dir=str(second_xcode(tmp_path)))
    unselected = by_name((await diagnose(mac.context(config=other))).results)
    assert unselected["xcode"].detail.endswith("(device.developer_dir)")


async def test_running_companions_each_say_which_xcode_they_run_with(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "a.pid").write_text(f"5001 777 SimMirror\n{mac.developer}")
    (run_dir / "b.pid").write_text("5002 777 SimMirror")
    (run_dir / "c.pid").write_text("5003 777 SimMirror")
    (run_dir / "d.pid").write_text("5004 777 SimMirror")

    async def command_of(pid: int) -> str | None:
        return {5001: "idb_companion --udid A", 5002: "/opt/homebrew/bin/idb_companion", 5004: "python3"}.get(pid)

    ctx = mac.context(run_dir=run_dir, pid_alive=lambda pid: pid != 5003, command_of=command_of)
    found = by_name((await diagnose(ctx)).results)["running companions"]
    assert found == CheckResult(
        "running companions", "ok", f"pid 5001 with {mac.developer}; pid 5002 with an Xcode it did not record"
    )
    nothing = by_name((await diagnose(mac.context(run_dir=tmp_path / "empty"))).results)["running companions"]
    assert nothing == CheckResult("running companions", "ok", "none")


async def _answer(code: int, out: str) -> tuple[int, str]:
    return code, out


async def test_frameworks_are_found_where_either_xcode_keeps_them_and_a_missing_one_warns(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    shared = mac.developer.parent / "SharedFrameworks" / "SimulatorKit.framework"
    (shared / "Resources" / "Info.plist").unlink()
    (shared / "Resources").rmdir()
    shared.rmdir()
    private = mac.developer / "Library" / "PrivateFrameworks" / "SimulatorKit.framework"
    private.mkdir(parents=True)
    found = by_name((await diagnose(mac.context(core_simulator=tmp_path / "nowhere"))).results)
    assert (
        found["simulator frameworks"].detail
        == "SimulatorKit version unknown in PrivateFrameworks; CoreSimulator version unknown"
    )
    private.rmdir()
    missing = by_name((await diagnose(mac.context())).results)["simulator frameworks"]
    assert missing.status == "warn" and missing.detail.startswith("SimulatorKit.framework is not in this Xcode")


async def test_runtimes_that_cannot_be_listed_or_include_no_ios_fail_with_the_fix(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.xcrun.on("simctl", "list", "runtimes", rc=1, err="CoreSimulatorService connection became invalid")
    listed = by_name((await diagnose(mac.context())).results)["runtimes"]
    assert listed.status == "fail" and "CoreSimulatorService" in listed.detail and listed.fix == FIRST_LAUNCH
    mac.xcrun.on("simctl", "list", "runtimes", out='{"runtimes": []}')
    none = by_name((await diagnose(mac.context())).results)["runtimes"]
    assert (none.status, none.detail, none.fix) == ("fail", "no iOS simulator runtime is installed", INSTALL_RUNTIME)


async def test_a_companion_named_but_not_runnable_fails_and_one_not_installed_leaves_a_view_only_mirror(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    configured = SimConfig.defaults().with_values(companion_path=str(tmp_path / "missing" / "idb_companion"))
    named = by_name((await diagnose(mac.context(config=configured))).results)["companion"]
    assert (
        named.status == "fail"
        and "cannot be run" in named.detail
        and "unset connectors.idb.companion_path" in named.fix
    )
    mac.installed = None
    absent = by_name((await diagnose(mac.context())).results)["companion"]
    assert absent.status == "warn" and absent.detail.startswith("not installed") and "brew install" in absent.fix
    mac.installed = str(mac.companion)
    del mac.answers[(str(mac.companion), "--version")]
    unknown = by_name((await diagnose(mac.context())).results)["companion"]
    assert unknown.detail == f"{mac.companion} (version unknown); it starts with {mac.developer}"


async def test_a_view_only_fallback_warns_and_no_usable_connector_fails(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    fallback = ConnectorRegistry(
        [FakeConnector("idb", available=False, reasons=("idb_companion is not installed",)),
         FakeConnector("simctl", capabilities=VIEW_ONLY)]
    )  # fmt: skip
    warned = by_name((await diagnose(mac.context(registry=fallback))).results)["connectors"]
    assert warned.status == "warn" and warned.detail.startswith("simctl is used (idb: idb_companion is not installed; ")
    nothing = ConnectorRegistry(
        [FakeConnector("idb", available=False, reasons=()), FakeConnector("simctl", available=False)]
    )
    failed = by_name((await diagnose(mac.context(registry=nothing))).results)["connectors"]
    assert failed.status == "fail" and failed.detail.startswith("none can be used (idb: ")


async def test_a_check_that_breaks_is_reported_as_failing_and_the_rest_still_run(tmp_path: Path) -> None:
    async def broken(ctx: DoctorContext) -> CheckResult:
        raise ConnectorUnavailable("boom")

    async def fine(ctx: DoctorContext) -> CheckResult:
        return CheckResult("fine", "ok", "fine")

    report = await diagnose(Mac(tmp_path).context(), [Check("broken", broken), Check("fine", fine)])
    assert report.results == (
        CheckResult("broken", "fail", "the check itself failed: boom"),
        CheckResult("fine", "ok", "fine"),
    )


# -- xcode tools -----------------------------------------------------------------------------------------------------

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
TOOLS = "xcode tools"
#: Stands for a runtime: the check only asks whether there is one, which is whether the doctor may use a device.
TAPPING: Any = object()


def tools_context(mac: Mac, bridge: FakeBridge | None = None, **changes: Any) -> DoctorContext:
    async def found(developer_dir: str) -> str:
        return f"{developer_dir}/usr/bin/mcpbridge"

    def reader(udid: str, developer_dir: str) -> BridgeReader:
        assert bridge is not None
        return BridgeReader(udid, developer_dir, client=BridgeClient(developer_dir, spawn=bridge.spawn), find=found)

    clock = ManualClock()
    ctx = mac.context(xcode=ChosenXcode(XCODE_27, "setting"), hierarchy_reader=reader, clock=clock, **changes)
    return ctx


async def test_the_xcode_tools_are_only_reported_on_until_a_scope_reads_the_screen_through_them(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    assert await check_xcode_tools(tools_context(mac)) == CheckResult(
        TOOLS, "ok", "this Xcode has no mcpbridge, which reading the screen through Xcode needs"
    )
    mac.xcrun.with_xcode("27.0")
    assert (
        await check_xcode_tools(tools_context(mac))
    ).detail == f"mcpbridge at {XCODE_27}/usr/bin/mcpbridge; not used"
    assert await check_xcode_tools(mac.context()) == CheckResult(TOOLS, "skip", "needs a working Xcode")


async def test_reading_through_an_xcode_before_27_fails_and_says_what_to_change(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    merged = SimConfig.defaults().with_values(mcpbridge_merge=True)
    result = await check_xcode_tools(tools_context(mac, config=merged))
    assert result.status == "fail" and result.detail.startswith(
        "connectors.mcpbridge.merge is on, but Reading the screen through Xcode needs Xcode 27 or later"
    )
    assert result.fix.startswith("Choose Xcode 27 with `sim-mirror config set device.developer_dir`")


async def test_a_scope_reading_through_xcode_27_has_a_booted_device_read_once(tmp_path: Path) -> None:
    mac = Mac(tmp_path)
    mac.xcrun.with_xcode("27.0")
    chosen = SimConfig.defaults().with_values(connector="mcpbridge")
    assert await check_xcode_tools(tools_context(mac, config=chosen)) == CheckResult(
        TOOLS,
        "skip",
        f"mcpbridge at {XCODE_27}/usr/bin/mcpbridge; connectors.preferred is mcpbridge, and reading needs a booted "
        "simulator",
    )
    bridge = FakeBridge(folder=tmp_path / "artifacts")
    ctx = tools_context(mac, bridge, config=chosen, runtime=TAPPING)
    result = await check_xcode_tools(ctx)
    assert result == CheckResult(
        TOOLS, "ok", f"read 149 elements of {BOOTED_UDID} through {XCODE_27}/usr/bin/mcpbridge in 0.0s"
    )
    assert bridge.tools()[-1] == "DeviceInteractionEndSession"
    named = await check_xcode_tools(tools_context(mac, bridge, config=chosen, runtime=TAPPING, device=BOOTED_UDID))
    assert f"of {BOOTED_UDID} through" in named.detail
    off = await check_xcode_tools(tools_context(mac, bridge, config=chosen, runtime=TAPPING, device="U-1"))
    assert off.status == "skip" and off.detail.endswith("reading needs a booted simulator")


async def test_a_scope_reading_through_xcode_hears_why_xcode_would_not_read_or_that_nothing_is_booted(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    mac.xcrun.with_xcode("27.0")
    merged = SimConfig.defaults().with_values(mcpbridge_merge=True)
    bridge = FakeBridge(folder=tmp_path / "artifacts").refuse(
        "DeviceInteractionStartSession", "This agent isn't approved"
    )
    result = await check_xcode_tools(tools_context(mac, bridge, config=merged, runtime=TAPPING))
    assert result.status == "fail" and result.detail.startswith(
        f"connectors.mcpbridge.merge is on, but Xcode's hierarchy of {BOOTED_UDID} could not be read: Xcode has not "
        "approved SimMirror"
    )
    mac.xcrun.on("simctl", "list", "devices", "-j", rc=1, err="simctl failed")
    assert (await check_xcode_tools(tools_context(mac, bridge, config=merged, runtime=TAPPING))).status == "skip"


async def test_screen_reading_reads_a_test_picture_with_the_xcode_in_use_unless_pixels_are_not_read(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    chosen = mac.context(xcode=ChosenXcode(str(mac.developer), "xcode-select"))
    result = await check_screen_reading(chosen)
    assert result.status == "ok" and result.detail.endswith("in 11 languages (fallback)")
    reading = mac.reader.readings()[0]
    assert reading["level"] == "accurate" and reading["image"]
    assert mac.reader.processes[0].returncode is not None
    assert [call.developer_dir for call in mac.xcrun.calls if "-O" in call.args] == [str(mac.developer)]
    off = dataclasses.replace(chosen, config=SimConfig.defaults().with_values(ocr_mode="off"))
    assert await check_screen_reading(off) == CheckResult(
        "screen reading", "ok", "perception.ocr is off: no screen is read from its pixels"
    )
    assert await check_screen_reading(mac.context()) == CheckResult("screen reading", "skip", "needs a working Xcode")


async def test_screen_reading_that_cannot_compile_or_misreads_warns_and_says_how_to_stop_reading_pixels(
    tmp_path: Path,
) -> None:
    mac = Mac(tmp_path)
    mac.xcrun.with_swift(compiles=False)
    chosen = mac.context(xcode=ChosenXcode(str(mac.developer), "xcode-select"))
    failed = await check_screen_reading(chosen)
    assert failed.status == "warn" and failed.detail.startswith(
        "perception.ocr is fallback, but the text reader could not be compiled: compiling sim-mirror-vision failed"
    )
    assert failed.fix.startswith("`sim-mirror config set perception.ocr off`")
    mac.xcrun.with_swift()
    mac.reader.lines = [{"text": "Tap to", "confidence": 1, "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]
    misread = await check_screen_reading(mac.context(xcode=ChosenXcode(str(mac.developer), "xcode-select")))
    assert misread == CheckResult(
        "screen reading", "warn", "the text reader read 'Tap to' in a picture of 'Tap to continue'", failed.fix
    )
    mac.reader.lines = []
    blank = await check_screen_reading(mac.context(xcode=ChosenXcode(str(mac.developer), "xcode-select")))
    assert blank.detail == "the text reader read 'nothing' in a picture of 'Tap to continue'"


def test_the_doctor_reads_with_the_text_reader_a_daemon_with_its_environment_keeps(tmp_path: Path) -> None:
    ctx = Mac(tmp_path).context(env={"SIM_MIRROR_STATE_DIR": str(tmp_path / "state")})
    assert vision_for(ctx)._folder == tmp_path / "state" / "helpers"
