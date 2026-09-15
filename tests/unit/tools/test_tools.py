# SPDX-License-Identifier: Apache-2.0
"""The agent tools: one catalogue offered by what the connector can do, answers shaped as MCP's, the device brought up
on first use, and what a call may reach checked first."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.core.actions import AgentActions
from sim_mirror.protocol import CLOSE_RESTARTING
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import FULL_CONTROL, JPEG, FakeConnector, made, no_wait
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, closer_log, scope
from sim_mirror.tools.context import READY_WAIT_S, ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, text

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")
REGISTRY = ToolRegistry()


def context(rig: DeviceRig, **changes: Any) -> ToolContext:
    actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait)
    ctx = ToolContext(
        manager=rig.manager,
        actions=actions,
        caller=CALLER,
        config=rig.config.get(CALLER.scope),
        copy=rig.copy,
        sleep=no_wait,
    )
    return dataclasses.replace(ctx, **changes)


async def use(rig: DeviceRig, name: object, arguments: object, **changes: Any) -> Result:
    return await REGISTRY.call(name, arguments, context(rig, **changes))


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


def folder(path: Path) -> Path:
    path.mkdir(parents=True)
    return path


# -- the catalogue ---------------------------------------------------------------------------------------------------


def test_every_tool_is_described_once_with_a_closed_schema_and_offered_only_where_it_can_work() -> None:
    on = dataclasses.replace(SimConfig.defaults(), build_tools=True)
    listed = REGISTRY.manifest(on, FULL_CONTROL)
    names = [tool["name"] for tool in listed["tools"]]
    assert names == ["sim_device", "sim_snapshot", "sim_screenshot", "sim_act", "sim_app", "sim_build_run", "sim_test"]
    assert REGISTRY.names() == names
    for tool in listed["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
    assert next(t for t in listed["tools"] if t["name"] == "sim_act")["inputSchema"]["required"] == ["steps"]
    assert "sim_snapshot" in listed["instructions"] and "sim_build_run" in listed["instructions"]
    without = REGISTRY.manifest(dataclasses.replace(on, build_tools=False), FULL_CONTROL)
    assert [tool["name"] for tool in without["tools"]] == names[:5]
    assert "xcodebuild" in without["instructions"] and "sim_build_run" not in without["instructions"]
    # A view-only mirror is offered nothing that reads or touches the screen.
    view_only = REGISTRY.manifest(on, VIEW_ONLY)
    assert [tool["name"] for tool in view_only["tools"]] == [
        "sim_device",
        "sim_screenshot",
        "sim_app",
        "sim_build_run",
        "sim_test",
    ]


async def test_a_call_to_no_tool_or_with_arguments_that_are_not_an_object_is_an_error(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    assert await use(rig, "sim_fly", {}) == text("there is no tool named 'sim_fly'", error=True)
    assert await use(rig, 42, {}) == text("there is no tool named 42", error=True)
    assert await use(rig, "sim_snapshot", ["full"]) == text("arguments must be an object", error=True)


async def test_a_tool_the_connector_cannot_serve_is_refused_with_the_connector_and_the_doctor(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False))
    refused = await use(rig, "sim_snapshot", {"mode": "full"})
    assert refused == text(
        "sim_snapshot needs element_tree, which the simctl connector showing this device cannot do. "
        "Run `sim-mirror doctor` to see why.",
        error=True,
    )
    assert "sim_act needs input_touch, which the simctl connector" in said(await use(rig, "sim_act", {"steps": []}))
    assert said(await use(rig, "sim_screenshot", {})) == "1 frame · 402x874px"


# -- the device ------------------------------------------------------------------------------------------------------


async def test_device_info_does_not_start_one_boot_does_and_both_give_the_build_destination(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    assert said(await use(rig, "sim_device", {})) == "No simulator is running here; sim_device boot starts one."
    booted = await use(rig, "sim_device", {"action": "boot"})
    assert booted["isError"] is False
    assert said(booted) == (
        f"SimMirror · alpha · tp-1 · iOS 26.5 · ready · 402x874pt @3x\nudid {made(1)}\n"
        f"build for: -destination 'platform=iOS Simulator,id={made(1)}'"
    )
    assert said(await use(rig, "sim_device", {"action": "info"})) == said(booted)
    assert said(await use(rig, "sim_device", {"action": "appearance", "mode": "dark"})) == "appearance dark"
    assert ("simctl", "ui", made(1), "appearance", "dark") in rig.argv()
    assert await use(rig, "sim_device", {"action": "appearance"}) == text(
        "appearance takes a mode of light or dark", error=True
    )
    assert await use(rig, "sim_device", {"action": "reboot"}) == text(
        "action is one of info, boot, restart, appearance", error=True
    )


async def test_restart_shuts_the_device_down_and_brings_it_back_unless_tests_are_running_on_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    # Nothing running yet: a restart is a boot.
    fresh = await use(rig, "sim_device", {"action": "restart"})
    assert said(fresh).startswith("restarted · SimMirror · alpha · tp-1 · iOS 26.5 · ready")
    assert ("simctl", "shutdown", made(1)) not in rig.argv()
    first = rig.manager.instance(CALLER.scope)
    assert first is not None
    closed, close = closer_log()
    rig.manager.attach(first, close)
    restarted = await use(rig, "sim_device", {"action": "restart"})
    assert restarted["isError"] is False and said(restarted).startswith("restarted · SimMirror · alpha · tp-1")
    assert ("simctl", "shutdown", made(1)) in rig.argv()
    # A window watching is told the device will be back, so it reconnects rather than offering to start it.
    assert closed == [(CLOSE_RESTARTING, "the simulator is restarting")]
    again = rig.manager.instance(CALLER.scope)
    assert again is not None and again is not first and again.state == "ready" and len(rig.idb.attached) == 2
    again.busy = "running tests (b1)"
    assert await use(rig, "sim_device", {"action": "restart"}) == text(
        "the device is busy: running tests (b1)", error=True
    )
    assert rig.manager.instance(CALLER.scope) is again


async def test_a_device_still_booting_is_waited_for_and_one_that_cannot_start_says_why(tmp_path: Path) -> None:
    rig = DeviceRig(folder(tmp_path / "waiting"), idb=FakeConnector("idb", hold=True))
    polls: list[float] = []

    async def release_later(seconds: float) -> None:
        polls.append(seconds)
        if len(polls) == 2:
            assert rig.idb.release is not None
            rig.idb.release.set()
        await asyncio.sleep(0)

    assert (await use(rig, "sim_snapshot", {"mode": "full"}, sleep=release_later))["isError"] is False
    assert len(polls) >= 2
    await rig.manager.shutdown()

    stuck = DeviceRig(folder(tmp_path / "stuck"), idb=FakeConnector("idb", hold=True))
    assert await use(stuck, "sim_snapshot", {}) == text(
        f"the simulator is still booting after {round(READY_WAIT_S)}s; call again shortly", error=True
    )
    await stuck.manager.shutdown()

    failing = DeviceRig(
        folder(tmp_path / "failing"), idb=FakeConnector("idb", fail=ConnectorUnavailable("no companion"))
    )
    assert await use(failing, "sim_snapshot", {}) == text("the simulator failed: no companion", error=True)
    failing.config.set(enabled=False)
    assert (
        said(await use(failing, "sim_snapshot", {}))
        == "The iOS Simulator is off for this project (`sim-mirror config`)."
    )


async def test_a_device_that_failed_without_saying_why_still_says_it_failed(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()

    async def fail_quietly(seconds: float) -> None:
        instance.state, instance.reason = "failed", None
        await asyncio.sleep(0)

    instance.state = "booting"
    assert await use(rig, "sim_snapshot", {}, sleep=fail_quietly) == text(
        "the simulator failed: no reason was given", error=True
    )


# -- looking and touching --------------------------------------------------------------------------------------------


async def test_snapshot_screenshot_and_act_answer_as_mcp_does(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    full = await use(rig, "sim_snapshot", {"mode": "full"})
    assert said(full).startswith("iOS 26.5 · Settings · 402x874pt · #") and full["isError"] is False
    assert await use(rig, "sim_snapshot", {"mode": "all"}) == text("mode is diff or full", error=True)
    shots = await use(rig, "sim_screenshot", {"frames": 2, "interval_ms": 100})
    assert said(shots) == "2 frames · 402x874px" and shots["isError"] is False
    image = {"type": "image", "data": base64.b64encode(JPEG).decode(), "mimeType": "image/jpeg"}
    assert shots["content"][1:] == [image, image]
    assert rig.idb.engine.screenshots[-1][0] == 400
    one = await use(rig, "sim_screenshot", {"width": 300})
    assert said(one) == "1 frame · 402x874px" and rig.idb.engine.screenshots[-1][0] == 300
    assert await use(rig, "sim_screenshot", {"width": 5000}) == text(
        "width must be a whole number of pixels from 160 to 1200", error=True
    )
    tapped = await use(rig, "sim_act", {"steps": [{"tap": "e2"}], "snapshot": "none"})
    assert tapped == text('ok tap e2 "General" (201,319)')
    missed = await use(rig, "sim_act", {"steps": [{"tap": "e99"}], "snapshot": "none"})
    assert missed["isError"] is True and said(missed).startswith("error step 1: e99 is not on screen now")
    assert await use(rig, "sim_act", {"steps": [{"tap": "e2"}], "snapshot": "some"}) == text(
        "snapshot is diff, full or none", error=True
    )
    assert (await use(rig, "sim_act", {"steps": "tap"}))["isError"] is True


# -- apps ------------------------------------------------------------------------------------------------------------


async def test_apps_are_launched_and_quit_under_a_caption_viewers_show(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.xcrun.on("simctl", "launch", out="com.acme.Notes: 81234\n")
    instance = await rig.up()
    events = instance.events.subscribe()
    launch = {"action": "launch", "bundle_id": "com.acme.Notes", "args": ["-UI"]}
    assert said(await use(rig, "sim_app", launch)) == "launched com.acme.Notes (pid 81234)"
    assert ("simctl", "launch", made(1), "com.acme.Notes", "-UI") in rig.argv()
    intent = events.get_nowait()
    assert intent["gesture"]["kind"] == "app" and intent["caption"] == "launch com.acme.Notes"
    rig.xcrun.on("simctl", "launch", out="launched\n")
    assert said(await use(rig, "sim_app", {"action": "launch", "bundle_id": "com.acme.Notes"})) == (
        "launched com.acme.Notes"
    )
    assert said(await use(rig, "sim_app", {"action": "terminate", "bundle_id": "com.acme.Notes"})) == (
        "terminated com.acme.Notes"
    )
    rig.xcrun.on("simctl", "launch", rc=1, err="FBSOpenApplicationServiceErrorDomain: not installed")
    failed = await use(rig, "sim_app", {"action": "launch", "bundle_id": "com.acme.Notes"})
    assert failed["isError"] is True and "not installed" in said(failed)


async def test_an_app_already_running_or_not_running_is_said_so_and_relaunch_starts_it_fresh(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.xcrun.on("simctl", "launch", out="com.acme.Notes: 81234\n")
    await rig.up()
    launch = {"action": "launch", "bundle_id": "com.acme.Notes"}
    assert said(await use(rig, "sim_app", launch)) == "launched com.acme.Notes (pid 81234)"
    # simctl brings a running app to the front and answers with the pid it already had: nothing started again.
    assert said(await use(rig, "sim_app", launch)) == (
        "com.acme.Notes was already running (pid 81234) and is in front again; relaunch: true starts it fresh"
    )
    rig.xcrun.on("simctl", "launch", out="com.acme.Notes: 81300\n")
    assert said(await use(rig, "sim_app", {**launch, "relaunch": True})) == "relaunched com.acme.Notes (pid 81300)"
    assert ("simctl", "launch", "--terminate-running-process", made(1), "com.acme.Notes") in rig.argv()
    assert said(await use(rig, "sim_app", {"action": "terminate", "bundle_id": "com.acme.Notes"})) == (
        "terminated com.acme.Notes"
    )
    # Quit, it is not "already running" if it comes back with the same pid.
    rig.xcrun.on("simctl", "launch", out="com.acme.Notes: 81300\n")
    assert said(await use(rig, "sim_app", launch)) == "launched com.acme.Notes (pid 81300)"
    rig.xcrun.on("simctl", "terminate", rc=3, err="found nothing to terminate")
    assert said(await use(rig, "sim_app", {"action": "terminate", "bundle_id": "com.example.NotesProbe"})) == (
        "com.example.NotesProbe was not running"
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"action": "launch", "bundle_id": "com.acme.Notes", "relaunch": "yes"}, "relaunch is true or false"),
        ({"action": "launch", "bundle_id": "-rm -rf"}, "bundle_id must be a bundle identifier"),
        ({"action": "launch", "bundle_id": "com.acme.Notes", "args": "-UI"}, "args must be a list"),
        ({"action": "launch", "bundle_id": "com.acme.Notes", "args": ["ok", 3]}, "args must be a list"),
        ({"action": "open_url", "url": "file:///etc/hosts"}, "file: URLs are not opened on the simulator"),
        ({"action": "open_url", "url": "JavaScript:alert(1)"}, "JavaScript: URLs are not opened"),
        ({"action": "open_url", "url": "prefs:root=General"}, "prefs: URLs are not opened"),
        ({"action": "open_url", "url": "App-Prefs:root=General"}, "App-Prefs: URLs are not opened"),
        ({"action": "open_url", "url": "no scheme here"}, "url must be a URL with a scheme"),
        ({"action": "open_url", "url": "https://" + "a" * 2000}, "url must be a URL with a scheme"),
        ({"action": "uninstall"}, "action is one of launch, terminate, install, open_url, logs"),
        ({"action": "logs", "since_s": 0}, "since_s must be a whole number from 1 to 300"),
        ({"action": "logs", "lines": 999}, "lines must be a whole number from 1 to 200"),
        ({"action": "logs", "filter": "x" * 201}, "filter is text of at most 200 characters"),
    ],
)
async def test_what_an_app_call_may_name_is_checked_first(
    tmp_path: Path, arguments: dict[str, Any], message: str
) -> None:
    rig = DeviceRig(tmp_path)
    answer = await use(rig, "sim_app", arguments)
    assert answer["isError"] is True and message in said(answer)


async def test_a_url_with_a_scheme_the_device_may_open_is_opened(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    assert said(await use(rig, "sim_app", {"action": "open_url", "url": "notes://new?title=Trip"})) == (
        "opened notes://new?title=Trip"
    )
    assert ("simctl", "openurl", made(1), "notes://new?title=Trip") in rig.argv()


def _app(parent: Path, name: str = "Notes.app") -> Path:
    app = folder(parent / name)
    (app / "Info.plist").write_text("<plist/>")
    return app


async def test_only_a_built_app_inside_an_allowed_folder_is_installed(tmp_path: Path) -> None:
    project = folder(tmp_path / "project")
    rig = DeviceRig(project)
    allowed = folder(project / "Build")
    app = _app(allowed)
    stray = _app(folder(tmp_path / "elsewhere"), "Stray.app")
    (allowed / "Link.app").symlink_to(stray)
    folder(allowed / "Plain.app")
    roots = (project,)
    installed = await use(rig, "sim_app", {"action": "install", "path": str(app)}, roots=roots)
    assert said(installed) == "installed Notes.app"
    assert ("simctl", "install", made(1), str(app.resolve())) in rig.argv()
    for path, message in (
        (str(stray), f"only an app built inside {project} can be installed"),
        (str(allowed / "Link.app"), "only an app built inside"),
        (str(allowed / "Plain.app"), "is not a built .app"),
        (str(allowed), "is not a built .app"),
        ("Build/Notes.app", "path must be the absolute path of a built .app"),
        (None, "path must be the absolute path"),
    ):
        answer = await use(rig, "sim_app", {"action": "install", "path": path}, roots=roots)
        assert answer["isError"] is True and message in said(answer), path
    nowhere = await use(rig, "sim_app", {"action": "install", "path": str(app)})
    assert said(nowhere) == "only an app built inside no folder here can be installed"


async def test_logs_are_read_for_an_app_or_for_errors_filtered_and_cut_to_the_last_lines(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    log = (
        "Timestamp               Ty Process[PID:TID]\n"
        "2026-09-15 10:00:01.000 E  Notes[81234:1] save failed\n"
        "\n"
        "2026-09-15 10:00:02.000 I  Notes[81234:1] saved draft\n"
        "2026-09-15 10:00:03.000 E  Notes[81234:1] save failed again\n"
    )
    rig.xcrun.on("simctl", "spawn", out=log)
    answer = await use(
        rig,
        "sim_app",
        {"action": "logs", "bundle_id": "com.acme.Notes", "filter": "FAILED", "lines": 1, "since_s": 30},
    )
    assert said(answer) == "2026-09-15 10:00:03.000 E  Notes[81234:1] save failed again"
    spawn = [args for args in rig.argv() if args[1] == "spawn"][-1]
    assert spawn[spawn.index("--last") + 1] == "30s"
    assert spawn[-1] == 'subsystem == "com.acme.Notes" OR process == "Notes"'
    everything = await use(rig, "sim_app", {"action": "logs"})
    assert len(said(everything).splitlines()) == 3
    assert [args for args in rig.argv() if args[1] == "spawn"][-1][-1] == "messageType == error OR messageType == fault"
    rig.xcrun.on("simctl", "spawn", out="Timestamp               Ty Process[PID:TID]\n")
    assert said(await use(rig, "sim_app", {"action": "logs"})) == "no log lines in the last 60s"
