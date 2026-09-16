# SPDX-License-Identifier: Apache-2.0
"""The build tools: built, installed and launched on the scope's device; tests keep the device; what is refused."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.events import Event
from sim_mirror.seams import Caller
from sim_mirror.storage.claims import Claims
from sim_mirror.testing.fakes import FakeProcess, fixture, made, no_wait
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result
from sim_mirror.tools.schemas import BUILD_WAIT_BOUNDS

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")
APP = "/Users/dev/probe-out/dd/Build/Products/Debug-iphonesimulator/NotesProbe.app"
BUNDLE = "com.example.probe.notes"
REGISTRY = ToolRegistry()


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


def drained(events: asyncio.Queue[Event]) -> list[Event]:
    return [events.get_nowait() for _ in range(events.qsize())]


class BuildRig:
    """A real manager and build runner over a fake Mac, whose xcodebuild ends with `rc` -- or runs until told."""

    def __init__(self, tmp_path: Path, *, rc: int | None = 0) -> None:
        self.sim = DeviceRig(tmp_path)
        self.folder = tmp_path / "NotesProbe"
        (self.folder / "NotesProbe.xcodeproj").mkdir(parents=True)
        (
            self.sim.xcrun.on("xcodebuild", "-list", out=fixture("xcodebuild-list.json"))
            .on("xcodebuild", "-showBuildSettings", out=fixture("xcodebuild-settings.json"))
            .on("xcresulttool", "get", "build-results", out=fixture("xcresult-build-ok.json"))
            .on("xcresulttool", "get", "test-results", "summary", out=fixture("xcresult-test-summary.json"))
            .on("xcresulttool", "get", "test-results", "tests", out=fixture("xcresult-test-tests.json"))
            .on("simctl", "launch", out=f"{BUNDLE}: 81234\n")
        )
        self.rc = rc
        self.started: list[tuple[str, ...]] = []
        self.processes: list[FakeProcess] = []
        self.runner = BuildRunner(self.sim.state, xcrun=self.sim.xcrun, start=self.start)
        self.actions = AgentActions(self.sim.manager, self.sim.config, clock=self.sim.clock, sleep=no_wait)

    async def start(self, *args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> FakeProcess:
        self.started.append(args)
        process = FakeProcess(5000 + len(self.processes))
        self.processes.append(process)
        if self.rc is not None:
            process.finish(self.rc)
        return process

    def context(self, **changes: Any) -> ToolContext:
        config = dataclasses.replace(self.sim.config.get(CALLER.scope), build_tools=True)
        ctx = ToolContext(
            manager=self.sim.manager,
            actions=self.actions,
            caller=CALLER,
            config=config,
            sleep=no_wait,
            builds=self.runner,
            folder=self.folder,
            shells_allowed=True,
        )
        return dataclasses.replace(ctx, **changes)

    async def call(self, name: str, arguments: dict[str, Any], **changes: Any) -> Result:
        return await REGISTRY.call(name, arguments, self.context(**changes))


async def test_a_build_is_built_for_the_scopes_device_then_installed_and_launched_there(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    answer = await rig.call("sim_build_run", {})
    lines = said(answer).splitlines()
    assert answer["isError"] is False
    assert lines[:3] == [
        "build ok · NotesProbe (Debug) · 6.9s · 0 errors, 0 warnings",
        "installed NotesProbe.app",
        f"launched {BUNDLE} (pid 81234)",
    ]
    assert lines[3].startswith("log ") and lines[3].endswith("-b1.log")
    assert f"platform=iOS Simulator,id={made(1)}" in rig.started[0] and rig.started[0][-1] == "build"
    argv = rig.sim.argv()
    assert any(args[1] == "install" and made(1) in args and APP in args for args in argv)
    assert any(args[1] == "launch" and made(1) in args and BUNDLE in args for args in argv)
    # Remembered, so a later sim_app launch of the app still running can say that is all it was.
    instance = rig.sim.manager.instance(CALLER.scope)
    assert instance is not None and instance.launched == {BUNDLE: 81234}


async def test_a_launch_that_names_no_pid_is_answered_without_one_and_remembers_nothing(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    rig.sim.xcrun.on("simctl", "launch", out="launched\n")
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[1:3] == ["installed NotesProbe.app", f"launched {BUNDLE}"]
    instance = rig.sim.manager.instance(CALLER.scope)
    assert instance is not None and instance.launched == {}


async def test_a_long_run_answers_with_its_id_and_only_its_own_scope_picks_it_up(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    assert said(await rig.call("sim_build_run", {"wait_s": 0})) == (
        "build b1 still running (0s) · call again with build_id b1"
    )
    other = await rig.call(
        "sim_build_run", {"build_id": "b1", "wait_s": 0}, caller=Caller(scope("tp-2"), "agent-2", "Codex")
    )
    assert other["isError"] is True and said(other) == "there is no build 'b1'; leave build_id out to start a run"
    assert said(await rig.call("sim_build_run", {"build_id": 7})) == "there is no build 7; recent runs here are b1"
    rig.processes[0].finish(0)
    assert said(await rig.call("sim_build_run", {"build_id": "b1"})).startswith("build ok")


async def test_warnings_are_asked_for_by_the_call_and_diagnostics_by_the_settings(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    refused = await rig.call("sim_build_run", {"warnings": "yes"})
    assert refused["isError"] is True and said(refused) == "warnings is true or false" and rig.started == []
    warned = '{"status": "succeeded", "warningCount": 1, "warnings": [{"message": "unused variable \'x\'"}]}'
    rig.sim.xcrun.on("xcresulttool", "get", "build-results", out=warned)
    lines = said(await rig.call("sim_build_run", {"warnings": True})).splitlines()
    assert lines[:2] == ["build ok · NotesProbe (Debug) · 0 errors, 1 warning", "warning unused variable 'x'"]
    await rig.call("sim_test", {"wait_s": 5})
    assert rig.started[-1][rig.started[-1].index("-collect-test-diagnostics") + 1] == "never"
    config = dataclasses.replace(rig.context().config, build_test_diagnostics=True)
    await rig.call("sim_test", {"wait_s": 5}, config=config)
    assert rig.started[-1][rig.started[-1].index("-collect-test-diagnostics") + 1] == "on-failure"


async def test_builds_are_refused_while_the_scope_does_not_allow_them_and_nothing_runs(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    off = dataclasses.replace(rig.context().config, build_tools=False)
    refusals: tuple[tuple[dict[str, Any], str], ...] = (
        ({"config": off}, "The iOS Simulator's build tools are off for this project (`sim-mirror config`)."),
        ({"shells_allowed": False}, "A build runs commands, and this project does not allow them"),
        ({"builds": None}, "The build runner is not running."),
        (
            {"folder": None},
            "There is no folder to build in for this project: start `sim-mirror mcp` in the project's "
            "folder, or give it `--root /path/to/the/project`.",
        ),
    )
    for changes, message in refusals:
        for tool in ("sim_build_run", "sim_test"):
            answer = await rig.call(tool, {}, **changes)
            assert answer["isError"] is True and message in said(answer)
    too_long = await rig.call("sim_build_run", {"wait_s": BUILD_WAIT_BOUNDS[1] + 1})
    assert said(too_long) == "wait_s must be a whole number from 0 to 600"
    assert rig.started == [] and rig.sim.argv() == []


async def test_a_folder_without_a_project_is_refused_with_how_to_name_one(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    answer = await rig.call("sim_build_run", {}, folder=tmp_path / "empty")
    assert answer["isError"] is True and "there is no Xcode project or workspace in" in said(answer)
    assert rig.started == []


async def test_a_build_that_cannot_be_installed_or_names_no_app_says_so_after_its_verdict(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    rig.sim.xcrun.on("simctl", "install", rc=1, err="An error was encountered processing the command (code=22)")
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[0].startswith("build ok") and lines[1].startswith("not launched: ")
    rig.sim.xcrun.on("xcodebuild", "-showBuildSettings", out="[]")
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[1] == (
        "not launched: NotesProbe builds no app to install -- a framework or a test bundle, say -- so there is nothing "
        "to launch; name a scheme that builds an iOS app"
    )


async def test_tests_keep_the_device_to_themselves_until_they_end(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    instance = await rig.sim.up()
    events = instance.events.subscribe()
    running = await rig.call("sim_test", {"wait_s": 0, "only_testing": ["NotesProbeTests"]})
    assert said(running) == "test b1 still running (0s) · call again with build_id b1"
    assert rig.started[0][-2:] == ("-only-testing:NotesProbeTests", "test")
    assert rig.sim.manager.instance(CALLER.scope) is instance
    assert instance.busy == "running tests (b1)"
    # Every screen watching is told, so a person sees why the device is not theirs to touch.
    assert [event["busy"] for event in drained(events) if event["type"] == "status"] == ["running tests (b1)"]
    refused = await rig.call("sim_act", {"steps": [{"tap": [10, 10]}]})
    assert refused["isError"] is True and "running tests (b1)" in said(refused)
    rig.processes[0].finish(65)
    lines = said(await rig.call("sim_test", {"build_id": "b1"})).splitlines()
    assert lines[0] == "test FAILED · NotesProbe (Debug) · 2 passed, 1 failed, 0 skipped · 34.3s"
    await asyncio.sleep(0)
    assert instance.busy is None
    assert [event["busy"] for event in drained(events) if event["type"] == "status"] == [None]


async def test_a_device_marked_busy_by_something_else_meanwhile_is_left_marked(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    await rig.call("sim_test", {"wait_s": 0})
    instance = rig.sim.manager.instance(CALLER.scope)
    assert instance is not None
    instance.busy = "recording"
    rig.processes[0].finish(0)
    await rig.call("sim_test", {"build_id": "b1"})
    await asyncio.sleep(0)
    assert instance.busy == "recording"


PRO_MAX = "3AEF58CA-1341-4C5D-A09A-EEB4CCDA8BD5"  # iPhone 17 Pro Max on iOS 26.5, in simctl-devices.json
SEVENTEEN_E = "2D961125-B1AB-402F-AABA-BE4B53837EF0"  # iPhone 17e on iOS 26.5


async def test_tests_sent_to_another_simulator_run_there_and_leave_the_scopes_own_device_alone(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    answer = await rig.call("sim_test", {"destination": {"name": "iPhone 17 Pro Max"}, "wait_s": 5})
    assert said(answer).startswith("test FAILED · NotesProbe (Debug) · on iPhone 17 Pro Max (iOS 26.5) · ")
    assert f"platform=iOS Simulator,id={PRO_MAX}" in rig.started[-1]
    # Nothing was brought up, booted or made for the scope: xcodebuild boots the destination itself.
    assert rig.sim.manager.instance(CALLER.scope) is None
    assert not any(args[1] in ("boot", "create", "shutdown") for args in rig.sim.argv())
    # The scope's own device, named as a destination, is simply the scope's device -- kept busy while tests run.
    instance = await rig.sim.up(CALLER.scope.id)
    rig.rc = None
    await rig.call("sim_test", {"destination": {"udid": instance.udid}, "wait_s": 0})
    assert instance.busy == "running tests (b2)" and f"platform=iOS Simulator,id={instance.udid}" in rig.started[-1]
    rig.processes[-1].finish(0)
    again = said(await rig.call("sim_test", {"build_id": "b2", "wait_s": 5}))
    assert again.startswith("test FAILED · NotesProbe (Debug) · 2 passed")


async def test_a_destination_somebody_else_is_using_is_refused_and_nothing_runs_there(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    others = Caller(scope("tp-2", "beta"), key="agent-2", title="Codex")
    theirs = await rig.sim.up("tp-2", "beta")
    named = next(choice for choice in await rig.sim.manager.devices(CALLER.scope) if choice["udid"] == theirs.udid)
    refused = await rig.call("sim_test", {"destination": {"udid": theirs.udid}})
    assert said(refused) == (
        f"{named['name']} ({named['runtime']}) is another project's simulator here; name one nobody is using"
    )
    await rig.call("sim_test", {"destination": {"udid": PRO_MAX}, "wait_s": 0}, caller=others)
    busy = await rig.call("sim_test", {"destination": {"name": "iPhone 17 Pro Max"}})
    assert said(busy) == "another project's tests are running on iPhone 17 Pro Max (iOS 26.5); name another simulator"
    rig.sim.alive_pids.add(4242)
    host = Claims(
        tmp_path / "claims", owner="Workbench", pid=4242, pid_alive=lambda pid: True, start_time=rig.sim._start_time
    )
    await host.acquire(SEVENTEEN_E)
    claimed = await rig.call("sim_test", {"destination": {"name": "iPhone 17e"}})
    assert said(claimed).startswith("Another Workbench on this Mac (pid 4242) is already showing this simulator.")
    assert len(rig.started) == 1


async def test_a_destination_is_for_tests_only_and_only_where_the_scope_can_have_a_simulator(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    build = await rig.call("sim_build_run", {"destination": {"name": "iPhone 17e"}})
    assert said(build) == "destination is for sim_test: a build is installed and launched on your own simulator"
    unknown = await rig.call("sim_test", {"destination": {"name": "iPhone 99"}})
    assert said(unknown).startswith("there is no simulator iPhone 99 on this Mac for this Xcode; there are ")
    rig.sim.policy.area = False
    off = await rig.call("sim_test", {"destination": {"name": "iPhone 17e"}})
    assert off["isError"] is True and said(off) == "The iOS Simulator is not available here."
    assert rig.started == []
