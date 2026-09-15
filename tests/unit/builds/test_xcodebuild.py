# SPDX-License-Identifier: Apache-2.0
"""Building and testing a scope's app: which project, one run per scope, answers from the result bundle, and ends."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.build import xcodebuild
from sim_mirror.build.xcodebuild import Build, BuildRefused, BuildRunner, changed, find_project
from sim_mirror.testing.fakes import BOOTED_UDID, FakeProcess, FakeXcrun, MemoryStateStore, fixture
from sim_mirror.testing.rig import scope

SCOPE = scope("topic-1", "ws")
APP = Path("/Users/dev/probe-out/dd/Build/Products/Debug-iphonesimulator/NotesProbe.app")
BUNDLE = "com.example.probe.notes"


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.folder = tmp_path / "NotesProbe"
        (self.folder / "NotesProbe.xcodeproj").mkdir(parents=True)
        self.state = MemoryStateStore(tmp_path)
        self.xcrun = (
            FakeXcrun()
            .on("xcodebuild", "-list", out=fixture("xcodebuild-list.json"))
            .on("xcodebuild", "-showBuildSettings", out=fixture("xcodebuild-settings.json"))
            .on("xcresulttool", "get", "build-results", out=fixture("xcresult-build-ok.json"))
            .on("xcresulttool", "get", "test-results", "summary", out=fixture("xcresult-test-summary.json"))
            .on("xcresulttool", "get", "test-results", "tests", out=fixture("xcresult-test-tests.json"))
        )
        self.started: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.processes: list[FakeProcess] = []
        self.signals: list[tuple[int, int]] = []
        self.start_error: OSError | None = None
        self.obeys = True
        self.runner = BuildRunner(
            self.state,
            xcrun=self.xcrun,
            start=self.start,
            clock=lambda: 100.0,
            wall=lambda: 0.0,
            signal_group=self.signal,
            stop_grace_s=0.01,
        )

    async def start(self, *args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> FakeProcess:
        if self.start_error is not None:
            raise self.start_error
        self.started.append((args, {"log_path": log_path, "developer_dir": developer_dir, "cwd": cwd}))
        process = FakeProcess(4000 + len(self.processes))
        self.processes.append(process)
        return process

    def signal(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        if self.obeys:
            next(process for process in self.processes if process.pid == pid).finish(-sig)

    async def begin(self, kind: str = "build", **options: Any) -> Build:
        return await self.runner.start(
            options.pop("scope", SCOPE),
            kind=kind,
            folder=self.folder,
            udid=BOOTED_UDID,
            developer_dir="/Applications/Xcode.app",
            configuration=options.pop("configuration", "Debug"),
            timeout_s=options.pop("timeout_s", 60),
            **options,
        )


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# -- which project ---------------------------------------------------------------------------------------------------


def test_the_one_project_in_a_folder_is_found_and_a_workspace_wins_over_the_project_inside_it(tmp_path: Path) -> None:
    (tmp_path / "App.xcodeproj").mkdir()
    (tmp_path / "App.xcodeproj.txt").touch()
    assert find_project(tmp_path).args == ("-project", str(tmp_path / "App.xcodeproj"))
    (tmp_path / "App.xcworkspace").mkdir()
    assert find_project(tmp_path).args == ("-workspace", str(tmp_path / "App.xcworkspace"))
    assert xcodebuild.DERIVED_DATA.name == "DerivedData"


def test_a_folder_with_none_or_several_asks_for_one_by_name(tmp_path: Path) -> None:
    with pytest.raises(BuildRefused, match="there is no Xcode project or workspace in"):
        find_project(tmp_path)
    (tmp_path / "One.xcodeproj").mkdir()
    (tmp_path / "Two.xcodeproj").mkdir()
    with pytest.raises(BuildRefused, match=r"several; name one: One\.xcodeproj, Two\.xcodeproj"):
        find_project(tmp_path)
    assert find_project(tmp_path, project="Two.xcodeproj").path == tmp_path / "Two.xcodeproj"


def test_a_named_project_must_be_one_and_must_be_inside_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "repo"
    (folder / "App.xcodeproj").mkdir(parents=True)
    (tmp_path / "Other.xcworkspace").mkdir()
    with pytest.raises(BuildRefused, match="not both"):
        find_project(folder, project="App.xcodeproj", workspace="App.xcworkspace")
    with pytest.raises(BuildRefused, match=r"App\.xcworkspace is not an Xcode workspace"):
        find_project(folder, workspace="App.xcworkspace")
    with pytest.raises(BuildRefused, match=r"App\.xcodeproj is not an Xcode workspace"):
        find_project(folder, workspace="App.xcodeproj")
    with pytest.raises(BuildRefused, match="must be inside"):
        find_project(folder, workspace="../Other.xcworkspace")


@pytest.mark.parametrize("named", [3, "App\x00.xcodeproj", "A" * 501 + ".xcodeproj"])
def test_a_project_named_as_anything_but_a_short_name_is_refused(tmp_path: Path, named: object) -> None:
    with pytest.raises(BuildRefused, match="project is the name of an Xcode project in"):
        find_project(tmp_path, project=named)


def test_a_project_whose_schemes_cannot_be_looked_through_still_answers_how_its_file_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "App.xcodeproj").mkdir()
    (tmp_path / "App.xcodeproj" / "project.pbxproj").write_text("// project")
    project = find_project(tmp_path)

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("Operation not permitted")

    monkeypatch.setattr(Path, "rglob", refuse)
    stamps = changed(project)
    assert len(stamps) == 2 and stamps[0] > 0 and stamps[1] == 0


def test_a_workspace_changes_with_the_projects_it_names_and_the_xcconfig_files_beside_it(tmp_path: Path) -> None:
    # A workspace was stamped by its own folder only, so a bundle id changed in its project installed the old app.
    workspace = tmp_path / "App.xcworkspace"
    workspace.mkdir()
    (workspace / "contents.xcworkspacedata").write_text(
        '<Workspace version = "1.0">\n   <FileRef location = "group:App.xcodeproj"></FileRef>\n'
        '   <FileRef location = "group:Pods/Pods.xcodeproj"></FileRef>\n</Workspace>\n'
    )
    app = tmp_path / "App.xcodeproj"
    app.mkdir()
    (app / "project.pbxproj").write_text("// app")
    (tmp_path / "Pods" / "Pods.xcodeproj").mkdir(parents=True)
    project = find_project(tmp_path)
    assert project.path == workspace

    def touch(path: Path, ns: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.utime(path, ns=(ns, ns))

    stamps = [changed(project)]
    touch(app / "project.pbxproj", 10)
    stamps.append(changed(project))
    touch(app / "xcshareddata" / "xcschemes" / "App-Dev.xcscheme", 20)
    stamps.append(changed(project))
    touch(tmp_path / "Config" / "Base.xcconfig", 30)
    stamps.append(changed(project))
    assert len(set(stamps)) == 4
    # Build output and hidden folders are not where build settings come from.
    for skipped in ("build", "DerivedData", "node_modules", ".git"):
        touch(tmp_path / skipped / "Ignored.xcconfig", 40)
    assert changed(project) == stamps[-1]
    # A workspace whose contents cannot be read still answers how it stands.
    (workspace / "contents.xcworkspacedata").unlink()
    assert changed(project)[1] == 0


# -- a build ---------------------------------------------------------------------------------------------------------


async def test_a_build_runs_xcodebuild_for_the_scopes_device_and_answers_what_it_built_then_what_after_did(
    rig: Rig,
) -> None:
    seen = []

    async def after(build: Build) -> list[str]:
        seen.append((build.app, build.bundle_id))
        return ["installed NotesProbe.app", f"launched {BUNDLE}"]

    build = await rig.begin(after=after)
    assert rig.runner.running(SCOPE.id) is build and build.id == "b1"
    bundle = rig.state.builds_dir(SCOPE) / build.bundle.name
    assert build.bundle == bundle and bundle.name.endswith("-b1.xcresult") and build.log == bundle.with_suffix(".log")
    ((args, options),) = rig.started
    assert args == (
        "xcodebuild",
        "-project",
        str(rig.folder / "NotesProbe.xcodeproj"),
        "-scheme",
        "NotesProbe",
        "-configuration",
        "Debug",
        "-destination",
        f"platform=iOS Simulator,id={BOOTED_UDID}",
        "-derivedDataPath",
        str(rig.state.derived_data(SCOPE)),
        "-resultBundlePath",
        str(bundle),
        "build",
    )
    assert options == {"log_path": build.log, "developer_dir": "/Applications/Xcode.app", "cwd": rig.folder}
    rig.processes[0].finish(0)
    answer = await rig.runner.result(SCOPE.id, "b1", wait_s=5)
    assert answer.splitlines() == [
        "build ok · NotesProbe (Debug) · 6.9s · 0 errors, 0 warnings",
        "installed NotesProbe.app",
        f"launched {BUNDLE}",
        f"log {build.log}",
    ]
    assert seen == [(APP, BUNDLE)] and build.state == "succeeded"
    assert rig.runner.running(SCOPE.id) is None
    settings_call = next(call for call in rig.xcrun.calls if call.args[1] == "-showBuildSettings")
    assert settings_call.cwd == str(rig.folder) and settings_call.developer_dir == "/Applications/Xcode.app"


async def test_an_unchanged_project_is_not_asked_for_its_schemes_or_its_app_again(rig: Rig, tmp_path: Path) -> None:
    products = tmp_path / "Products"
    (products / "NotesProbe.app").mkdir(parents=True)
    settings = [
        {
            "buildSettings": {
                "WRAPPER_NAME": "NotesProbe.app",
                "TARGET_BUILD_DIR": str(products),
                "PRODUCT_BUNDLE_IDENTIFIER": BUNDLE,
            }
        }
    ]
    rig.xcrun.on("xcodebuild", "-showBuildSettings", out=json.dumps(settings))

    async def after(build: Build) -> list[str]:
        return [f"app {build.app} {build.bundle_id}"]

    def asked() -> tuple[int, int]:
        return (
            sum(call.args[1] == "-list" for call in rig.xcrun.calls),
            sum(call.args[1] == "-showBuildSettings" for call in rig.xcrun.calls),
        )

    async def built() -> str:
        build = await rig.begin(after=after)
        rig.processes[-1].finish(0)
        return (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).splitlines()[1]

    said = f"app {products / 'NotesProbe.app'} {BUNDLE}"
    assert [await built(), await built()] == [said, said]
    assert asked() == (1, 1)
    # An edit to the project -- its file, or a scheme a level or two down -- asks both again.
    (rig.folder / "NotesProbe.xcodeproj" / "project.pbxproj").write_text("// changed")
    assert await built() == said and asked() == (2, 2)
    schemes = rig.folder / "NotesProbe.xcodeproj" / "xcuserdata" / "me.xcuserdatad" / "xcschemes"
    schemes.mkdir(parents=True)
    (schemes / "NotesProbe.xcscheme").write_text("<Scheme/>")
    assert await built() == said and asked() == (3, 3)
    # An app no longer where the settings said is looked for again.
    (products / "NotesProbe.app").rmdir()
    await built()
    assert asked() == (3, 4)


async def test_a_build_still_going_answers_with_its_id_and_a_later_call_picks_it_up(rig: Rig) -> None:
    await rig.begin()
    assert (
        await rig.runner.result(SCOPE.id, "b1", wait_s=0) == "build b1 still running (0s) · call again with build_id b1"
    )
    with pytest.raises(BuildRefused, match="build b1 is still running here"):
        await rig.begin()
    with pytest.raises(BuildRefused, match="there is no build 'b1'"):
        await rig.runner.result("another-scope", "b1", wait_s=0)
    rig.processes[0].finish(0)
    assert (await rig.runner.result(SCOPE.id, "b1", wait_s=5)).startswith("build ok · NotesProbe (Debug)")
    with pytest.raises(BuildRefused, match="there is no build 'b9'"):
        await rig.runner.result(SCOPE.id, "b9", wait_s=0)


async def test_a_broken_build_lists_its_errors_and_neither_reads_settings_nor_launches(rig: Rig) -> None:
    rig.xcrun.on("xcresulttool", "get", "build-results", out=fixture("xcresult-build-broken.json"))

    async def after(build: Build) -> list[str]:  # pragma: no cover - a failed build never launches
        raise AssertionError("launched a broken build")

    build = await rig.begin(after=after)
    rig.processes[0].finish(65)
    lines = (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).splitlines()
    assert lines[:2] == [
        "build FAILED · NotesProbe (Debug) · 1.5s · 1 error, 0 warnings",
        "error /Users/dev/NotesProbe/Sources/NotesApp.swift:39:19 Cannot convert value of type 'String' to "
        "specified type 'Int'",
    ]
    assert build.state == "failed" and build.app is None
    assert not any(call.args[1] == "-showBuildSettings" for call in rig.xcrun.calls)


async def test_a_build_that_left_no_result_bundle_points_at_its_log(rig: Rig) -> None:
    rig.xcrun.on("xcresulttool", "get", "build-results", rc=1, err="error: no such bundle")
    build = await rig.begin()
    rig.processes[0].finish(70)
    assert (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).splitlines() == [
        "build b1 ended without a result to read (NotesProbe (Debug)); the log says why",
        f"log {build.log}",
    ]
    assert build.state == "failed"


async def test_an_app_the_settings_do_not_name_is_not_launched_and_a_refused_launch_is_said(rig: Rig) -> None:
    rig.xcrun.on(
        "xcodebuild",
        "-showBuildSettings",
        out='[{"buildSettings": {"WRAPPER_NAME": "Kit.framework", "TARGET_BUILD_DIR": "/x"}}, "odd", {}]',
    )

    async def after(build: Build) -> list[str]:
        assert build.app is None and build.bundle_id is None
        raise BuildRefused("the build made no app to install")

    build = await rig.begin(after=after)
    rig.processes[0].finish(0)
    assert (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).splitlines()[1] == (
        "not launched: the build made no app to install"
    )
    rig.xcrun.on("xcodebuild", "-showBuildSettings", out='{"not": "a list"}')
    second = await rig.begin()
    rig.processes[1].finish(0)
    await rig.runner.result(SCOPE.id, second.id, wait_s=5)
    assert second.app is None and second.id == "b2"


# -- schemes, names and refusals -------------------------------------------------------------------------------------


async def test_a_scheme_is_the_only_one_or_the_one_named_and_otherwise_asked_for(rig: Rig) -> None:
    rig.xcrun.on("xcodebuild", "-list", out='{"workspace": {"name": "App", "schemes": ["App", "AppKit", 3]}}')
    with pytest.raises(BuildRefused, match="several schemes; name one: App, AppKit"):
        await rig.begin()
    with pytest.raises(BuildRefused, match="has no scheme Widget; it has App, AppKit"):
        await rig.begin(scheme="Widget")
    build = await rig.begin(scheme="AppKit")
    assert build.scheme == "AppKit" and build.label == "AppKit (Debug)"
    rig.processes[0].finish(0)
    await rig.runner.result(SCOPE.id, build.id, wait_s=5)
    # Schemes change with the project: an edit to it is what makes the listing worth asking for again.
    (rig.folder / "NotesProbe.xcodeproj" / "project.pbxproj").write_text("// the schemes went away")
    rig.xcrun.on("xcodebuild", "-list", out='{"project": {"schemes": []}}')
    with pytest.raises(BuildRefused, match="has no schemes to build"):
        await rig.begin()
    with pytest.raises(BuildRefused, match="has no scheme App; it has none"):
        await rig.begin(scheme="App")


async def test_a_project_xcodebuild_cannot_list_is_refused_with_what_it_said(rig: Rig) -> None:
    rig.xcrun.on("xcodebuild", "-list", rc=74, err="xcodebuild: error: The project is damaged")
    with pytest.raises(BuildRefused, match=r"could not list NotesProbe\.xcodeproj: xcodebuild: error: The project is"):
        await rig.begin()
    rig.xcrun.on("xcodebuild", "-list", out="User defaults from command line:\nnot json")
    with pytest.raises(BuildRefused, match="could not list"):
        await rig.begin()
    rig.xcrun.on("xcodebuild", "-list", out='["odd"]')
    with pytest.raises(BuildRefused, match="has no schemes to build"):
        await rig.begin()
    assert rig.started == []


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"kind": "archive"}, "a run is one of build, test"),
        ({"configuration": "Debug; rm -rf"}, "configuration must be a name"),
        ({"scheme": "-destination"}, "scheme must be a name"),
        ({"only_testing": "AppTests"}, "only_testing is a list"),
        ({"skip_testing": ["App Tests/x"]}, "skip_testing is a list"),
        ({"only_testing": ["T"] * 51}, "only_testing is a list of at most 50"),
    ],
)
async def test_what_would_reach_xcodebuild_as_anything_but_a_name_is_refused(
    rig: Rig, options: dict[str, Any], message: str
) -> None:
    kind = options.pop("kind", "test")
    with pytest.raises(BuildRefused, match=message):
        await rig.begin(kind, **options)
    assert rig.started == []


async def test_xcodebuild_that_cannot_start_is_refused_and_leaves_the_scope_free(rig: Rig) -> None:
    rig.start_error = FileNotFoundError("Xcode command-line tools are not installed (no xcrun)")
    with pytest.raises(BuildRefused, match="xcodebuild could not be started: Xcode command-line tools"):
        await rig.begin()
    assert rig.runner.running(SCOPE.id) is None


# -- a test run ------------------------------------------------------------------------------------------------------


async def test_a_test_run_passes_its_selection_on_and_answers_with_its_failures(rig: Rig) -> None:
    build = await rig.begin(
        "test", only_testing=["NotesProbeTests/NotesTests"], skip_testing=["NotesProbeUITests"], after=None
    )
    args = rig.started[0][0]
    assert args[-3:] == ("-only-testing:NotesProbeTests/NotesTests", "-skip-testing:NotesProbeUITests", "test")
    rig.processes[0].finish(65)
    lines = (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).splitlines()
    assert lines[0] == "test FAILED · NotesProbe (Debug) · 2 passed, 1 failed, 0 skipped · 34.3s"
    assert lines[1].startswith("fail NotesTests/testDeliberatelyFails() NotesTests.swift:10 XCTAssertEqual failed")
    assert build.state == "failed"


async def test_a_test_run_without_a_report_says_so_and_one_without_its_tree_still_counts(rig: Rig) -> None:
    rig.xcrun.on("xcresulttool", "get", "test-results", "summary", rc=1)
    build = await rig.begin("test")
    rig.processes[0].finish(65)
    assert (await rig.runner.result(SCOPE.id, build.id, wait_s=5)).startswith("test b1 ended without a result to read")
    rig.xcrun.on("xcresulttool", "get", "test-results", "summary", out='{"result": "Passed", "passedTests": 3}')
    rig.xcrun.on("xcresulttool", "get", "test-results", "tests", rc=1)
    second = await rig.begin("test")
    rig.processes[1].finish(0)
    assert (await rig.runner.result(SCOPE.id, second.id, wait_s=5)).splitlines()[0] == (
        "test ok · NotesProbe (Debug) · 3 passed, 0 failed, 0 skipped"
    )
    assert second.state == "succeeded"


# -- ending a run ----------------------------------------------------------------------------------------------------


async def test_a_run_past_its_timeout_is_ended_with_its_process_group(rig: Rig) -> None:
    build = await rig.begin(timeout_s=0.01)
    answer = await rig.runner.result(SCOPE.id, build.id, wait_s=5)
    assert answer == f"build b1 was stopped after 0s\nlog {build.log}"
    assert rig.signals == [(4000, signal.SIGTERM)] and build.state == "timed_out"
    assert rig.runner.running(SCOPE.id) is None


async def test_a_run_that_ignores_term_is_killed(rig: Rig) -> None:
    rig.obeys = False
    build = await rig.begin(timeout_s=0.01)
    await rig.runner.result(SCOPE.id, build.id, wait_s=5)
    assert rig.signals == [(4000, signal.SIGTERM), (4000, signal.SIGKILL)]


async def test_cancelling_ends_the_scopes_run_and_a_scope_with_none_has_nothing_to_cancel(rig: Rig) -> None:
    assert await rig.runner.cancel(SCOPE.id) is False
    build = await rig.begin()
    await asyncio.sleep(0)
    assert await rig.runner.cancel(SCOPE.id) is True
    assert build.state == "cancelled" and rig.signals == [(4000, signal.SIGTERM)]
    assert await rig.runner.result(SCOPE.id, build.id, wait_s=0) == f"build b1 was cancelled\nlog {build.log}"
    assert rig.runner.running(SCOPE.id) is None


async def test_cancelling_while_launching_does_not_signal_a_build_that_already_ended(rig: Rig) -> None:
    launching = asyncio.Event()

    async def after(build: Build) -> list[str]:
        launching.set()
        await asyncio.Event().wait()
        return []  # pragma: no cover - cancelled first

    await rig.begin(after=after)
    rig.processes[0].finish(0)
    await launching.wait()
    await rig.runner.shutdown()
    assert rig.signals == [] and rig.runner.running(SCOPE.id) is None


async def test_switching_a_group_off_ends_its_runs_and_no_other_groups(rig: Rig) -> None:
    mine = await rig.begin()
    theirs = await rig.begin(scope=scope("topic-9", "other"))
    await asyncio.sleep(0)
    assert await rig.runner.cancel_group("ws") == 1
    assert mine.state == "cancelled" and theirs.state == "running"
    assert await rig.runner.cancel_group("ws") == 0
    await rig.runner.shutdown()
    assert theirs.state == "cancelled"


async def test_a_scope_keeps_only_its_last_finished_runs_readable(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xcodebuild, "KEEP_FINISHED", 2)
    ids: list[str] = []
    for _ in range(5):
        build = await rig.begin()
        rig.processes[-1].finish(0)
        assert build.task is not None
        await build.task
        ids.append(build.id)
    with pytest.raises(BuildRefused, match="there is no build"):
        await rig.runner.result(SCOPE.id, ids[0], 0)
    assert await rig.runner.result(SCOPE.id, ids[3], 0) and await rig.runner.result(SCOPE.id, ids[4], 0)
