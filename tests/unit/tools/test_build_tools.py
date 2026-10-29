# SPDX-License-Identifier: Apache-2.0
"""The build tools: built, installed and launched on the scope's device; tests keep the device; what is refused."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import plistlib
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sim_mirror.build.source import Source, read_source
from sim_mirror.build.stamp import DIRTY_KEY, SHA_KEY, STAMPED
from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.control import InstalledApp
from sim_mirror.core.events import Event
from sim_mirror.core.signing import SigningTeams
from sim_mirror.platform.keychain import Team
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.seams import Caller
from sim_mirror.storage.claims import Claims
from sim_mirror.testing.checkouts import linked_worktree, main_checkout
from sim_mirror.testing.fakes import (
    PHONE_UDID,
    FakeConnector,
    FakePhoneBackend,
    FakeProcess,
    fixture,
    made,
    no_wait,
)
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

    def __init__(self, tmp_path: Path, *, rc: int | None = 0, **devices: Any) -> None:
        self.sim = DeviceRig(tmp_path, **devices)
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
        #: The folder each xcodebuild ran in.
        self.ran_in: list[Path | None] = []
        self.processes: list[FakeProcess] = []
        #: What the build folder's checkout says of itself; None reads no source at all.
        self.status: str | None = None
        self.runner = BuildRunner(self.sim.state, xcrun=self.sim.xcrun, start=self.start, source=self.source)
        self.actions = AgentActions(self.sim.manager, self.sim.config, clock=self.sim.clock, sleep=no_wait)

    async def source(self, folder: Path) -> Source | str:
        async def status(argv: Sequence[str]) -> tuple[int, str]:
            return 0, self.status or ""

        return "" if self.status is None else await read_source(folder, status)

    async def start(self, *args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> FakeProcess:
        self.started.append(args)
        self.ran_in.append(cwd)
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


async def test_a_build_and_a_test_run_say_which_folder_branch_and_commit_they_built(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    rig.status = "# branch.oid 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d\n# branch.head feature/login\n? New.swift\n"
    source = f"source {rig.folder} · feature/login @ 1a2b3c4 (dirty)"
    built = said(await rig.call("sim_build_run", {})).splitlines()
    assert built[-2] == source and built[-1].startswith("log ")
    assert Path(built[-1].removeprefix("log ")).read_text(encoding="utf-8").splitlines()[0] == source
    rig.status = "# branch.oid 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d\n# branch.head (detached)\n"
    tested = said(await rig.call("sim_test", {"wait_s": 5})).splitlines()
    assert tested[-2] == f"source {rig.folder} · detached HEAD @ 1a2b3c4"


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


def worktree_of(rig: BuildRig, tmp_path: Path) -> Path:
    """The rig's project as a repository's main checkout, and a worktree of it beside it, with the project too."""
    main_checkout(rig.folder)
    worktree = linked_worktree(rig.folder, tmp_path.resolve() / "NotesProbe-feature")
    (worktree / "NotesProbe.xcodeproj").mkdir()
    return worktree


def ran_with(rig: BuildRig, key: str) -> str:
    return rig.started[-1][rig.started[-1].index(key) + 1]


async def test_a_build_runs_in_the_worktree_the_agents_client_works_in_not_where_simmirror_started(
    tmp_path: Path,
) -> None:
    rig = BuildRig(tmp_path)
    worktree = worktree_of(rig, tmp_path)
    in_worktree = Caller(CALLER.scope, CALLER.key, CALLER.title, roots=(worktree / "Sources",))
    for tool in ("sim_build_run", "sim_test"):
        answer = await rig.call(tool, {"wait_s": 5}, caller=in_worktree)
        assert said(answer).startswith(f"{tool.removeprefix('sim_').removesuffix('_run')} ")
        assert rig.ran_in[-1] == worktree
        assert ran_with(rig, "-project") == str(worktree / "NotesProbe.xcodeproj")
    # A client working in the folder SimMirror started in, or saying nothing, builds that folder as before.
    for caller in (Caller(CALLER.scope, CALLER.key, CALLER.title, roots=(rig.folder,)), CALLER):
        lines = said(await rig.call("sim_build_run", {}, caller=caller)).splitlines()
        assert lines[0].startswith("build ok") and rig.ran_in[-1] == rig.folder


async def test_a_build_runs_in_the_folder_the_call_names_when_it_is_one_the_scope_may_build(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    worktree = worktree_of(rig, tmp_path)
    (rig.folder / "Nested").mkdir()
    (rig.folder / "Nested" / "Nested.xcodeproj").mkdir()
    assert said(await rig.call("sim_build_run", {"folder": str(worktree)})).startswith("build ok")
    assert rig.ran_in[-1] == worktree
    await rig.call("sim_build_run", {"folder": "Nested"})
    assert rig.ran_in[-1] == rig.folder.resolve() / "Nested"
    await rig.call("sim_build_run", {"folder": str(rig.folder)})
    assert rig.ran_in[-1] == rig.folder
    count = len(rig.started)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Evil.xcodeproj").mkdir()
    refused = await rig.call("sim_build_run", {"folder": str(outside)})
    assert refused["isError"] is True and said(refused) == (
        f"{outside} is not a folder builds may run in here: they run in {rig.folder}, a folder inside it, or the same "
        "folder of another checkout of its repository, such as a git worktree"
    )
    missing = await rig.call("sim_test", {"folder": str(rig.folder / "Missing")})
    assert said(missing) == f"{rig.folder.resolve() / 'Missing'} is not a folder"
    for bad in ("", 7, "x" * 1025, "a\x00b"):
        answer = await rig.call("sim_build_run", {"folder": bad})
        assert said(answer) == "folder is the path of the folder to build in, at most 1024 characters"
    assert len(rig.started) == count


async def test_a_client_working_where_the_scope_may_not_build_is_told_what_was_built_instead(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    elsewhere = Caller(CALLER.scope, CALLER.key, CALLER.title, roots=(tmp_path / "other-project",))
    lines = said(await rig.call("sim_build_run", {}, caller=elsewhere)).splitlines()
    assert lines[0] == (
        f"note: built {rig.folder}, not {tmp_path / 'other-project'} where your client works, which builds here may "
        "not reach: name the folder with folder, or start `sim-mirror mcp` in the project's folder, or give it "
        "`--root /path/to/the/project`"
    )
    assert lines[1].startswith("build ok") and rig.ran_in[-1] == rig.folder


async def test_a_build_in_another_folder_runs_with_that_folders_settings(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    worktree = worktree_of(rig, tmp_path)
    asked: list[Path] = []

    def settings_for(folder: Path) -> Any:
        asked.append(folder)
        return dataclasses.replace(rig.context().config, build_configuration="Release")

    await rig.call("sim_build_run", {}, settings_for=settings_for)
    assert ran_with(rig, "-configuration") == "Debug" and asked == []
    await rig.call("sim_build_run", {"folder": str(worktree)}, settings_for=settings_for)
    assert ran_with(rig, "-configuration") == "Release" and asked == [worktree]


def staging_scheme(folder: Path) -> None:
    schemes = folder / "NotesProbe.xcodeproj" / "xcshareddata" / "xcschemes"
    schemes.mkdir(parents=True)
    (schemes / "Probe (Staging).xcscheme").write_text(fixture("xcscheme-staging.xcscheme"), encoding="utf-8")


async def test_a_build_in_a_worktree_reads_the_scheme_there_and_keeps_its_settings_configuration(
    tmp_path: Path,
) -> None:
    rig = BuildRig(tmp_path)
    rig.sim.xcrun.on("xcodebuild", "-list", out=fixture("xcodebuild-list-names.json"))
    worktree = worktree_of(rig, tmp_path)
    staging_scheme(worktree)
    arguments = {"scheme": "Probe (Staging)", "folder": str(worktree)}
    await rig.call("sim_build_run", arguments)
    assert ran_with(rig, "-configuration") == "Staging"
    await rig.call("sim_build_run", {"scheme": "Probe (Staging)"})
    assert ran_with(rig, "-configuration") == "Debug"

    def settings_for(folder: Path) -> Any:
        return dataclasses.replace(rig.context().config, build_configuration="Release")

    await rig.call("sim_build_run", arguments, settings_for=settings_for)
    assert ran_with(rig, "-configuration") == "Release"


async def test_a_build_naming_no_configuration_builds_the_one_its_scheme_runs_unless_the_settings_name_one(
    tmp_path: Path,
) -> None:
    """A scheme whose Run action builds Staging: a build in Debug would install a second app, with a bundle id of its
    own, beside the one the person uses."""
    rig = BuildRig(tmp_path)
    rig.sim.xcrun.on("xcodebuild", "-list", out=fixture("xcodebuild-list-names.json"))
    staging_scheme(rig.folder)
    lines = said(await rig.call("sim_build_run", {"scheme": "Probe (Staging)"})).splitlines()
    assert rig.started[-1][5:7] == ("-configuration", "Staging")
    assert lines[0] == "build ok · Probe (Staging) (Staging, its scheme's) · 6.9s · 0 errors, 0 warnings"
    config = dataclasses.replace(rig.context().config, build_configuration="Release")
    lines = said(await rig.call("sim_build_run", {"scheme": "Probe (Staging)"}, config=config)).splitlines()
    assert rig.started[-1][5:7] == ("-configuration", "Release")
    assert lines[0].startswith("build ok · Probe (Staging) (Release) · ")
    await rig.call("sim_build_run", {"scheme": "Probe (Staging)", "configuration": "Debug"}, config=config)
    assert rig.started[-1][5:7] == ("-configuration", "Debug")


async def test_a_build_of_another_app_than_the_expected_one_is_not_installed_and_both_are_named(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    config = dataclasses.replace(rig.context().config, build_expected_bundle_id="com.example.probe.notes.staging")
    answer = await rig.call("sim_build_run", {}, config=config)
    lines = said(answer).splitlines()
    assert lines[0].startswith("build ok") and lines[1] == (
        f"not launched: NotesProbe (Debug) builds {BUNDLE}, not com.example.probe.notes.staging, the app "
        "build.expected_bundle_id names, so it was not installed: name the configuration that builds "
        "com.example.probe.notes.staging with configuration, or change build.expected_bundle_id"
    )
    assert not any(args[1] in ("install", "launch") for args in rig.sim.argv())
    config = dataclasses.replace(config, build_expected_bundle_id=BUNDLE)
    lines = said(await rig.call("sim_build_run", {}, config=config)).splitlines()
    assert lines[1:3] == ["installed NotesProbe.app", f"launched {BUNDLE} (pid 81234)"]


async def test_a_worktrees_own_expected_bundle_id_is_the_one_held_to(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    worktree = worktree_of(rig, tmp_path)

    def settings_for(folder: Path) -> Any:
        return dataclasses.replace(rig.context().config, build_expected_bundle_id="com.example.probe.notes.staging")

    lines = said(await rig.call("sim_build_run", {"folder": str(worktree)}, settings_for=settings_for)).splitlines()
    assert lines[1].startswith(f"not launched: NotesProbe (Debug) builds {BUNDLE}, not com.example.probe.notes.staging")
    assert not any(args[1] == "install" for args in rig.sim.argv())
    lines = said(await rig.call("sim_build_run", {}, settings_for=settings_for)).splitlines()
    assert lines[1] == "installed NotesProbe.app"


async def test_a_test_run_whose_tests_run_in_another_app_than_the_expected_one_is_not_started(tmp_path: Path) -> None:
    """xcodebuild installs a test host itself: held to the setting only after, a second app would already be there."""
    rig = BuildRig(tmp_path)
    rig.sim.xcrun.on("xcodebuild", "-showBuildSettings", "test", out=fixture("xcodebuild-settings-test.json"))
    worktree = worktree_of(rig, tmp_path)

    def settings_for(folder: Path) -> Any:
        return dataclasses.replace(rig.context().config, build_expected_bundle_id=BUNDLE)

    answer = await rig.call("sim_test", {"folder": str(worktree)}, settings_for=settings_for)
    assert answer["isError"] is True and said(answer).startswith(
        f"NotesProbe (Debug) runs NotesProbeTests in NotesProbe, which is com.example.probe.notes.staging, not {BUNDLE}"
    )
    assert rig.started == [] and rig.sim.manager.instance(CALLER.scope).busy is None
    # The build folder has no such setting of its own, so its tests run.
    answer = await rig.call("sim_test", {}, settings_for=settings_for)
    assert answer["isError"] is True and len(rig.started) == 1  # the fixture has a failed test
    assert answer["structuredContent"]["state"] == "failed"


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


async def test_a_device_nothing_here_builds_for_is_refused_before_it_is_brought_up(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    rig.sim.memory.choose(CALLER.scope, False, "avd-Pixel-9-3fa2c1")
    answer = await rig.call("sim_build_run", {})
    assert answer["isError"] is True and said(answer) == "nothing here builds for this android device"
    assert rig.started == [] and rig.sim.manager.instance(CALLER.scope) is None


async def test_a_real_device_s_build_is_signed_by_the_team_its_scope_signs_with_when_the_project_names_none(
    tmp_path: Path,
) -> None:
    rig = BuildRig(tmp_path, phones=FakePhoneBackend(), phone=FakeConnector("phone", kinds=frozenset({"physical"})))
    await rig.sim.manager.choose(CALLER.scope, PHONE_UDID)

    def mac(*teams: str) -> SigningTeams:
        far = datetime(2126, 1, 1, tzinfo=timezone.utc)

        async def listed() -> list[Team]:
            return [Team(team, team, far) for team in teams]

        return SigningTeams(lambda _: None, listed)

    await rig.call("sim_build_run", {}, signing=mac("MACTEAM001"))
    assert rig.started[-1][-3:] == ("-allowProvisioningUpdates", "DEVELOPMENT_TEAM=MACTEAM001", "build")
    await rig.call("sim_build_run", {}, signing=mac("TEAMAAAAA1", "TEAMBBBBB2"))
    assert rig.started[-1][-2:] == ("-allowProvisioningUpdates", "build"), "none known: the project's own signing"
    named = dataclasses.replace(rig.context().config, real_devices_team_id="SETTEAM001")
    await rig.call("sim_build_run", {}, config=named)
    assert "DEVELOPMENT_TEAM=SETTEAM001" in rig.started[-1], "a host without signing uses the setting"


async def test_a_real_device_s_build_in_a_worktree_is_signed_as_the_worktree_s_project_says_not_the_build_folder_s(
    tmp_path: Path,
) -> None:
    rig = BuildRig(tmp_path, phones=FakePhoneBackend(), phone=FakeConnector("phone", kinds=frozenset({"physical"})))
    await rig.sim.manager.choose(CALLER.scope, PHONE_UDID)
    worktree = worktree_of(rig, tmp_path)
    (rig.folder / "NotesProbe.xcodeproj" / "project.pbxproj").write_text("DEVELOPMENT_TEAM = OLDTEAM001;\n")
    (worktree / "NotesProbe.xcodeproj" / "project.pbxproj").write_text("CODE_SIGN_STYLE = Automatic;\n")

    async def listed() -> list[Team]:
        return [Team("MACTEAM001", "Me", datetime(2126, 1, 1, tzinfo=timezone.utc))]

    teams = SigningTeams(lambda _: rig.folder, listed)
    # The worktree's branch names no team: the build folder's is not imposed on it.
    await rig.call("sim_build_run", {"folder": str(worktree)}, signing=teams)
    assert rig.ran_in[-1] == worktree and "DEVELOPMENT_TEAM=MACTEAM001" in rig.started[-1]
    await rig.call("sim_build_run", {}, signing=teams)
    assert rig.started[-1][-2:] == ("-allowProvisioningUpdates", "build"), "the build folder's project signs itself"


# -- the commit stamped into the app ---------------------------------------------------------------------------------

COMMIT = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def stamping(rig: BuildRig, tmp_path: Path) -> tuple[Path, list[dict[str, Any]], ToolContext]:
    """A build that makes a real, signed app; what each install found in the Info.plist of the app it was given; and
    a context whose settings stamp it."""
    products = tmp_path / "dd" / "Build" / "Products" / "Debug-iphonesimulator"
    app = products / "NotesProbe.app"
    (app / "_CodeSignature").mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": BUNDLE, "CFBundleVersion": "7"}))
    settings = json.loads(fixture("xcodebuild-settings.json"))
    settings[0]["buildSettings"]["TARGET_BUILD_DIR"] = str(products)
    rig.sim.xcrun.on("xcodebuild", "-showBuildSettings", out=json.dumps(settings))
    installed: list[dict[str, Any]] = []

    def install(args: tuple[str, ...]) -> XcrunResult:
        installed.append({"path": args[-1], **plistlib.loads((Path(args[-1]) / "Info.plist").read_bytes())})
        return XcrunResult(0, "", "")

    rig.sim.xcrun.on("simctl", "install", then=install)
    config = dataclasses.replace(rig.context().config, build_stamp_source=True)
    return app, installed, rig.context(config=config)


async def test_a_stamped_build_installs_a_signed_copy_holding_its_commit_and_keeps_none(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    app, installed, ctx = stamping(rig, tmp_path)
    rig.status = f"# branch.oid {COMMIT}\n# branch.head main\n? New.swift\n"
    lines = said(await REGISTRY.call("sim_build_run", {}, ctx)).splitlines()
    assert lines[1:4] == [
        "installed NotesProbe.app",
        "stamped 1a2b3c4 with changes not committed into its Info.plist (SIM_MIRROR_SOURCE_SHA)",
        f"launched {BUNDLE} (pid 81234)",
    ]
    copy = app.parent.parent / STAMPED / "NotesProbe.app"
    assert installed == [
        {"path": str(copy), "CFBundleIdentifier": BUNDLE, "CFBundleVersion": "7", SHA_KEY: COMMIT, DIRTY_KEY: True}
    ]
    signed = next(args for args in rig.sim.argv() if args[0] == "codesign")
    assert signed[:4] == ("codesign", "--force", "--sign", "-") and signed[-1] == str(copy)
    assert not copy.exists(), "the device keeps its own copy"
    assert SHA_KEY not in plistlib.loads((app / "Info.plist").read_bytes()), "the app in DerivedData is as built"
    # Off, the app is installed as it was built.
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[1:3] == ["installed NotesProbe.app", f"launched {BUNDLE} (pid 81234)"]
    assert installed[-1]["path"] == str(app) and SHA_KEY not in installed[-1]


async def test_a_build_that_cannot_be_stamped_is_installed_as_built_and_says_why(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    app, installed, ctx = stamping(rig, tmp_path)
    lines = said(await REGISTRY.call("sim_build_run", {}, ctx)).splitlines()
    assert lines[2] == f"not stamped: git named no commit for {rig.folder}"
    rig.status = f"# branch.oid {COMMIT}\n# branch.head main\n"
    rig.sim.xcrun.on("codesign", rc=1, err="Notes.app: errSecInternalComponent")
    lines = said(await REGISTRY.call("sim_build_run", {}, ctx)).splitlines()
    assert lines[2] == "not stamped: it could not be signed again: Notes.app: errSecInternalComponent"
    assert [entry["path"] for entry in installed] == [str(app), str(app)]


async def test_a_real_devices_build_is_never_stamped(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, phones=FakePhoneBackend(), phone=FakeConnector("phone", kinds=frozenset({"physical"})))
    await rig.sim.manager.choose(CALLER.scope, PHONE_UDID)
    rig.status = f"# branch.oid {COMMIT}\n# branch.head main\n"
    config = dataclasses.replace(rig.context().config, build_stamp_source=True)
    lines = said(await rig.call("sim_build_run", {}, config=config)).splitlines()
    assert "not stamped: a real device's app is signed for it, and SimMirror does not sign it again" in lines
    assert not any(args[0] == "codesign" for args in rig.sim.argv())


def installed_app(rig: BuildRig, tmp_path: Path, info: dict[str, Any]) -> Path:
    """An app on the simulator, where simctl says its bundle is."""
    bundle = tmp_path / "device" / "Bundle" / "NotesProbe.app"
    bundle.mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(plistlib.dumps(info))
    rig.sim.xcrun.on("simctl", "get_app_container", out=f"{bundle}\n")
    return bundle


async def test_app_info_says_which_commit_the_installed_app_was_built_from_and_whether_it_is_the_folders(
    tmp_path: Path,
) -> None:
    rig = BuildRig(tmp_path)
    info = {"CFBundleName": "NotesProbe", "CFBundleShortVersionString": "1.2", "CFBundleVersion": "7", SHA_KEY: COMMIT}
    bundle = installed_app(rig, tmp_path, info)
    rig.status = f"# branch.oid {COMMIT}\n# branch.head main\n"
    lines = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})).splitlines()
    assert lines == [
        f"{BUNDLE} · NotesProbe 1.2 (7) · {bundle}",
        "stamped 1a2b3c4 (SIM_MIRROR_SOURCE_SHA)",
        f"the code in {rig.folder}: 1a2b3c4, with nothing uncommitted then or now",
    ]
    assert ("simctl", "get_app_container", made(1), BUNDLE, "app") in rig.sim.argv()
    rig.status = "# branch.oid 9f8e7d6c5b4a39281706f5e4d3c2b1a098f7e6d5\n# branch.head main\n"
    lines = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})).splitlines()
    assert lines[2].startswith(f"not the code in {rig.folder}, which is at 9f8e7d6 now: build again")
    # Compared with the folder the call names, as a build there would be.
    worktree = worktree_of(rig, tmp_path)
    named = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE, "folder": str(worktree)}))
    assert f"not the code in {worktree}," in named
    # Where nothing builds, the stamp is all there is to say.
    for nothing_builds in ({"builds": None}, {"shells_allowed": False}):
        lines = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE}, **nothing_builds)).splitlines()
        assert lines[1:] == ["stamped 1a2b3c4 (SIM_MIRROR_SOURCE_SHA)"]


async def test_app_info_says_when_there_is_no_stamp_or_no_app(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path)
    bundle = installed_app(rig, tmp_path, {"CFBundleVersion": "7"})
    lines = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})).splitlines()
    assert lines == [
        f"{BUNDLE} · NotesProbe 7 · {bundle}",
        "no stamp: it was installed with build.stamp_source off, or not by sim_build_run",
    ]
    (bundle / "Info.plist").write_bytes(b"spoilt")
    assert said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})).startswith(f"{BUNDLE} · NotesProbe")
    missing = "An error was encountered (domain=NSPOSIXErrorDomain, code=2): No such file or directory"
    rig.sim.xcrun.on("simctl", "get_app_container", rc=2, err=missing)
    gone = await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})
    assert gone["isError"] is False and said(gone).startswith(f"{BUNDLE} is not installed on ")
    assert (await rig.call("sim_app", {"action": "info"}))["isError"] is True


async def test_app_info_on_a_real_device_says_what_devicectl_knows_and_that_it_carries_no_stamp(tmp_path: Path) -> None:
    phones = FakePhoneBackend()
    rig = BuildRig(tmp_path, phones=phones, phone=FakeConnector("phone", kinds=frozenset({"physical"})))
    await rig.sim.manager.choose(CALLER.scope, PHONE_UDID)
    phones.control_for.apps[BUNDLE] = InstalledApp(BUNDLE, "NotesProbe", "2.1", "file:///private/var/NotesProbe.app/")
    lines = said(await rig.call("sim_app", {"action": "info", "bundle_id": BUNDLE})).splitlines()
    assert lines == [
        f"{BUNDLE} · NotesProbe 2.1 · file:///private/var/NotesProbe.app/",
        "no stamp: a real device's app is never stamped, and its Info.plist is not read off the device",
    ]


async def test_build_results_distinguish_compilation_installation_launch_and_pending_jobs(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    started = await rig.call("sim_build_run", {"wait_s": 0})
    assert not started["isError"]
    assert started["structuredContent"]["state"] == "running"
    build_id = started["structuredContent"]["build_id"]
    rig.processes[0].finish(0)
    rig.sim.xcrun.on("simctl", "launch", rc=1, err="launch refused")
    failed = await rig.call("sim_build_run", {"build_id": build_id})
    assert len(rig.started) == 1, "polling must not build twice"
    assert failed["isError"] and "not launched" in said(failed)
    assert failed["structuredContent"]["state"] == "succeeded", "compilation itself succeeded"
    assert failed["structuredContent"]["installed"] is True
    assert failed["structuredContent"]["launched"] is False
    assert failed["structuredContent"]["folder"] == str(rig.folder)
    rig.sim.xcrun.on("simctl", "launch", out=f"{BUNDLE}: 81234\n")
    rig.rc = 0
    success = await rig.call("sim_build_run", {})
    assert not success["isError"]
    assert success["structuredContent"]["installed"] and success["structuredContent"]["launched"]


async def test_cancelled_build_is_an_error_when_polled_and_keeps_its_id(tmp_path: Path) -> None:
    rig = BuildRig(tmp_path, rc=None)
    started = await rig.call("sim_build_run", {"wait_s": 0})
    await asyncio.sleep(0)
    assert await rig.runner.cancel(CALLER.scope.id)
    result = await rig.call("sim_build_run", {"build_id": started["structuredContent"]["build_id"]})
    assert result["isError"] and result["structuredContent"]["state"] == "cancelled"
    assert not result["structuredContent"]["installed"]
