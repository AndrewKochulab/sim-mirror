# SPDX-License-Identifier: Apache-2.0
"""The composition root: SimMirror put together from seams, started and closed, told of changed settings, and the one
answer to whether a scope's agents may use their tools now."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.connectors.app.merge import AppHierarchyMerge
from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.mcpbridge.merge import HierarchyMerge
from sim_mirror.core.android_backend import AndroidBackend
from sim_mirror.core.android_devices import AndroidServices
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.recordings import RecordingOptions
from sim_mirror.core.runtime import Runtime, default_recordings
from sim_mirror.core.screen_relay import ScreenRelay
from sim_mirror.perception.readers import CombinedExtraReaders
from sim_mirror.perception.vision.helper import VisionHelpers
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import (
    BOOTED_UDID,
    FakeConnector,
    FakeProcess,
    FakeXcrun,
    MemoryStateStore,
    StaticConfig,
    fixture_udid,
    no_wait,
)
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.testing.vision import FakeTextRecognizer, text_line
from sim_mirror.tools.device import VERSION_LINE, device_line

CALLER = Caller(scope("tp-1"), key="agent-1", title="Codex")


class Started:
    """A build runner whose xcodebuild runs until a test ends it, and whose ending signals nothing real."""

    def __init__(self, rig: DeviceRig) -> None:
        self.processes: list[FakeProcess] = []
        self.runner = BuildRunner(
            rig.state, xcrun=rig.xcrun, start=self.start, signal_group=self.signal, source=self.source
        )

    async def source(self, folder: Path) -> str:
        return ""

    async def start(self, *args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> FakeProcess:
        process = FakeProcess(6000 + len(self.processes))
        self.processes.append(process)
        return process

    def signal(self, pid: int, sig: int) -> None:
        next(process for process in self.processes if process.pid == pid).finish(-sig)


class Hierarchy:
    closed = False

    def readers(self, udid: str, connector: str, config: object) -> tuple[()]:
        return ()

    def forget(self, udid: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def runtime_over(
    rig: DeviceRig,
    builds: BuildRunner | None = None,
    hierarchy: Hierarchy | None = None,
    *,
    vision: FakeTextRecognizer | None = None,
) -> Runtime:
    return Runtime.build(
        config=rig.config,
        state=rig.state,
        memory=rig.memory,
        policy=rig.policy,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        builds=builds,
        xcrun=rig.xcrun,
        hierarchy=hierarchy,
        vision=vision or FakeTextRecognizer(),
        clock=rig.clock,
        sleep=no_wait,
        platform="darwin",
    )


def test_a_runtime_without_a_registry_finds_the_built_in_connectors(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = Runtime.build(
        config=StaticConfig(), state=MemoryStateStore(tmp_path), memory=rig.memory, policy=rig.policy
    )
    assert {"idb", "simctl", "mcpbridge"} <= set(runtime.registry.names())
    assert runtime.tools.names()[0] == "sim_device" and runtime.builds.runs() == []
    extra = runtime.actions._extra
    assert isinstance(extra, CombinedExtraReaders) and [type(part) for part in extra._parts] == [
        AppHierarchyMerge,
        HierarchyMerge,
    ]
    assert isinstance(runtime.actions._pixels._recognizer, VisionHelpers)
    assert isinstance(runtime.manager.backend("android"), AndroidBackend)


def test_a_scope_that_remembers_no_device_gets_a_simulator_unless_the_mac_has_android_and_no_xcode(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    sdk = tmp_path / "sdk"
    sdk.mkdir()

    def built(has_xcode: bool, sdk_path: str) -> Runtime:
        android = AndroidServices.build(rig.state)
        android.sdk_path = sdk_path
        return Runtime.build(
            config=StaticConfig(),
            state=rig.state,
            memory=rig.memory,
            policy=rig.policy,
            android=android,
            has_xcode=lambda: has_xcode,
        )

    runtime = built(True, str(sdk))
    android = runtime.manager.backend("android").services  # type: ignore[attr-defined]
    reattached: list[str] = []
    runtime.manager.reattach = reattached.append  # type: ignore[method-assign]
    for udid, kind, capabilities in (
        ("avd-Pixel-9-3fa2c1", "android", frozenset()),
        ("avd-Live-000000", "android", frozenset({Capability.STREAM_H264})),
        ("D946616B-6E4F-4F5C-8C76-54FAD9B7D702", "simulator", frozenset()),
    ):
        runtime.manager._instances[udid] = DeviceInstance(
            udid=udid, name=udid, runtime="", owner=CALLER.scope, developer_dir="", scopes=set(), created=False,
            connector="x", capabilities=capabilities, kind=kind,  # type: ignore[arg-type]
        )  # fmt: skip
    android.jars.on_ready()
    assert reattached == ["avd-Pixel-9-3fa2c1"], "scrcpy set up: only an Android device shown through adb alone"
    assert built(True, str(sdk)).manager.kind(CALLER.scope) == "simulator"
    assert built(False, str(sdk)).manager.kind(CALLER.scope) == "android"
    assert built(False, str(tmp_path / "none")).manager.kind(CALLER.scope) == "simulator"


async def test_starting_ends_what_an_earlier_run_left_and_closing_stops_everything(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    hierarchy = Hierarchy()
    runtime = runtime_over(rig, hierarchy=hierarchy)
    await runtime.start()
    assert rig.idb.reaped == 1 and runtime.reaper.running
    instance = await runtime.manager.ensure(CALLER.scope)
    assert instance.task is not None
    await instance.task
    await runtime.close()
    assert not runtime.reaper.running and rig.idb.closed == [instance.udid] and hierarchy.closed


async def test_an_agent_is_offered_its_tools_only_while_the_simulator_and_its_agent_tools_are_on(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    offered = await runtime.manifest(CALLER.scope)
    assert [tool["name"] for tool in offered["tools"]] == [
        "sim_device",
        "sim_snapshot",
        "sim_screenshot",
        "sim_act",
        "sim_app",
        "sim_record",
    ]
    said = await runtime.call(CALLER, "sim_device", {})
    assert said["content"][0]["text"] == f"No simulator is running here; sim_device boot starts one.\n{VERSION_LINE}"
    rig.config.set(agent_tools=False)
    off = "The iOS Simulator's agent tools are off for this project (`sim-mirror config`)."
    assert await runtime.manifest(CALLER.scope) == {"tools": [], "instructions": off}
    assert (await runtime.call(CALLER, "sim_device", {}))["isError"] is True
    rig.config.set(agent_tools=True, enabled=False)
    refused = await runtime.call(CALLER, "sim_device", {})
    assert refused["content"][0]["text"] == "The iOS Simulator is off for this project (`sim-mirror config`)."


async def test_a_call_tells_the_screens_its_agent_is_working_before_and_after_it_runs(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    instance = await runtime.manager.ensure(CALLER.scope)
    assert instance.task is not None
    await instance.task
    events = instance.events.subscribe()
    await runtime.call(CALLER, "sim_device", {})
    published = [events.get_nowait() for _ in range(events.qsize())]
    phases = [event.get("phase") for event in published if event["type"] == "agent"]
    assert phases[0] == phases[-1] == "working" and published[0]["agent"] == {"key": "agent-1", "title": "Codex"}
    rig.config.set(enabled=False)
    await runtime.call(CALLER, "sim_device", {})
    assert [event for _ in range(events.qsize()) if (event := events.get_nowait())["type"] == "agent"] == []


async def test_a_view_only_mirror_lists_every_tool_and_refuses_a_touch_saying_why_and_what_puts_it_right(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False))
    every = ["sim_device", "sim_snapshot", "sim_screenshot", "sim_act", "sim_app", "sim_record"]
    runtime = runtime_over(rig)
    assert [tool["name"] for tool in (await runtime.manifest(CALLER.scope))["tools"]] == every
    rig.config.set(ocr_mode="off")
    assert [tool["name"] for tool in (await runtime.manifest(CALLER.scope))["tools"]] == every
    refused = await runtime.call(CALLER, "sim_act", {"steps": [{"tap": [100, 200]}]})
    said = refused["content"][0]["text"]
    assert refused["isError"] is True and said.startswith("sim_act needs input_touch, which the simctl connector")
    assert "(the idb connector is switched off in this test)" in said and said.endswith(rig.copy.doctor_hint)


async def test_an_agent_snapshots_a_view_only_mirror_from_its_pixels_and_is_refused_once_its_scope_reads_none(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False))
    await rig.up()
    runtime = runtime_over(rig, vision=FakeTextRecognizer([text_line("Sign in", 150, 400, 100, 20)]))
    said = await runtime.call(CALLER, "sim_snapshot", {"mode": "full"})
    assert said["isError"] is False and 'e1 text "Sign in" (200,410)' in said["content"][0]["text"]
    rig.config.set(ocr_mode="off")
    refused = await runtime.call(CALLER, "sim_snapshot", {"mode": "full"})
    assert refused["isError"] is True
    assert refused["content"][0]["text"].startswith("sim_snapshot needs element_tree, which the simctl connector")


async def test_closing_lets_go_of_what_reads_pixels(tmp_path: Path) -> None:
    vision = FakeTextRecognizer()
    runtime = runtime_over(DeviceRig(tmp_path), vision=vision)
    await runtime.close()
    assert vision.closed


def test_a_call_runs_with_the_hosts_policy_as_it_is_now(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.policy.roots, rig.policy.folder, rig.policy.shells = (tmp_path,), tmp_path / "app", True
    ctx = runtime.tool_context(CALLER)
    assert (ctx.roots, ctx.folder, ctx.shells_allowed) == ((tmp_path,), tmp_path / "app", True)
    assert ctx.builds is runtime.builds and ctx.caller is CALLER and ctx.copy is rig.copy


def test_a_build_in_another_folder_runs_with_that_folders_own_settings_when_a_person_gave_it_some(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    project, worktree, plain = tmp_path / "app", tmp_path / "app-feature", tmp_path / "app-other"
    file = tmp_path / "config.toml"
    file.write_text(
        f'[build]\nconfiguration = "QA"\n[scopes."{Scope.for_folder(worktree).id}".build]\nconfiguration = "Release"\n'
        f'[scopes."{Scope.for_folder(project).id}".build]\nconfiguration = "Staging"\n'
    )
    runtime = Runtime.build(
        config=TomlConfigSource(file, env={}),
        state=rig.state,
        memory=rig.memory,
        policy=rig.policy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        vision=FakeTextRecognizer(),
        sleep=no_wait,
        platform="darwin",
    )
    ctx = runtime.tool_context(Caller(Scope.for_folder(project), "agent-1", "Codex"))
    assert ctx.settings_for is not None
    assert [ctx.settings_for(folder).build_configuration for folder in (worktree, plain, project)] == [
        "Release",
        "Staging",
        "Staging",
    ]
    # Settings that cannot say where each value comes from give the scope's own.
    static = runtime_over(rig).tool_context(CALLER).settings_for
    assert static is not None and static(worktree) == rig.config.get(CALLER.scope)


async def test_settings_switched_off_end_the_builds_that_may_not_run_now_and_only_those(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(build_tools=True)
    (tmp_path / "App.xcodeproj").mkdir()
    rig.xcrun.on("xcodebuild", "-list", out='{"project": {"schemes": ["App"]}}')
    started = Started(rig)
    runtime = runtime_over(rig, started.runner)

    async def begin(scope_id: str, group: str = "alpha") -> None:
        await runtime.builds.start(
            scope(scope_id, group), kind="build", folder=tmp_path, udid="U", developer_dir="", configuration="Debug",
            timeout_s=60,
        )  # fmt: skip

    await begin("tp-1")
    await begin("tp-9", "beta")
    await asyncio.sleep(0)
    await runtime.reconcile("alpha")
    assert len(runtime.builds.runs()) == 2
    rig.config.set_for("tp-1", build_tools=False)
    await runtime.reconcile("beta")
    assert len(runtime.builds.runs()) == 2
    await runtime.reconcile("alpha")
    assert [run.scope.id for run in runtime.builds.runs()] == ["tp-9"]
    rig.config.set(enabled=False)
    await runtime.reconcile()
    assert runtime.builds.runs() == []
    await runtime.close()


async def test_a_screen_socket_is_relayed_with_its_owners_settings(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    instance = await rig.up()
    relay = runtime.relay(FakeXcrun(), instance)  # type: ignore[arg-type]
    assert isinstance(relay, ScreenRelay)
    await runtime.close()


async def test_builds_the_host_no_longer_allows_commands_for_are_ended_and_the_reaper_does_it_too(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(build_tools=True)
    (tmp_path / "App.xcodeproj").mkdir()
    rig.xcrun.on("xcodebuild", "-list", out='{"project": {"schemes": ["App"]}}')
    started = Started(rig)
    runtime = runtime_over(rig, started.runner)
    await runtime.builds.start(
        scope("tp-1"), kind="build", folder=tmp_path, udid="U", developer_dir="", configuration="Debug", timeout_s=60
    )
    await asyncio.sleep(0)
    assert runtime.reaper._jobs == [runtime.stop_builds_not_allowed]
    await runtime.stop_builds_not_allowed()
    assert len(runtime.builds.runs()) == 1
    rig.policy.shells = False
    await runtime.stop_builds_not_allowed()
    assert runtime.builds.runs() == []
    await runtime.close()


async def test_by_default_a_simulator_is_recorded_by_simctl_on_its_own_xcode(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(recording_folder=str(tmp_path / "recordings"), developer_dir="/X.app/Contents/Developer")
    instance = await rig.up()
    started: list[tuple[tuple[str, ...], str]] = []

    async def start(*args: str, log_path: Path, developer_dir: str) -> FakeProcess:
        started.append((args, developer_dir))
        log_path.write_text("Recording started\n")
        process = FakeProcess(0)
        process.finish(0)
        return process

    recordings = default_recordings(rig.xcrun, rig.copy, start=start)
    config = rig.config.get(instance.owner)
    run = await recordings.start(
        instance, config, rig.manager.control(instance), by="Ann", options=RecordingOptions.from_config(config)
    )
    assert run.watchdog is not None
    run.watchdog.cancel()
    [(args, xcode)] = started
    assert args[:5] == ("simctl", "io", instance.udid, "recordVideo", "--codec=h264")
    assert xcode == "/X.app/Contents/Developer" and Path(args[-1]).parent == tmp_path / "recordings"


async def test_a_host_serves_the_tools_from_its_own_mcp_server_with_manifest_and_call_as_promised(
    tmp_path: Path,
) -> None:
    """`Runtime.manifest` and `Runtime.call` answer in MCP's own shapes, which `docs/stability.md` promises for 3.x: a
    host can hand them to its own MCP server's ``tools/list`` and ``tools/call`` unchanged."""
    import json

    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    listed = await runtime.manifest(CALLER.scope)
    assert set(listed) == {"tools", "instructions"} and isinstance(listed["instructions"], str)
    assert listed["tools"] and all(set(tool) == {"name", "description", "inputSchema"} for tool in listed["tools"])
    assert all(tool["inputSchema"]["type"] == "object" for tool in listed["tools"])
    json.dumps(listed)  # what a host puts on the wire as it is
    answered = await runtime.call(CALLER, "sim_device", {})
    assert {"content", "isError"} <= set(answered) <= {"content", "isError", "structuredContent"}
    assert answered["isError"] is False
    assert all(part["type"] in {"text", "image"} for part in answered["content"])
    for name, arguments in (("no_such_tool", {}), ("sim_device", "not an object")):
        refused = await runtime.call(CALLER, name, arguments)
        assert refused["isError"] is True and refused["content"][0]["type"] == "text"
    rig.config.set(enabled=False)
    off = await runtime.manifest(CALLER.scope)
    assert off["tools"] == [] and off["instructions"]


# -- a device of each agent's own -----------------------------------------------------------------------------------

A = Caller(scope("tp-1"), key="agent-a", title="Claude Code #aaaa")
B = Caller(scope("tp-1"), key="agent-b", title="Claude Code #bbbb")
PRO_MAX = fixture_udid("iPhone 17 Pro Max")
LAUNCH = {"action": "launch", "bundle_id": "com.example.demo"}


def said(result: dict[str, object]) -> str:
    content = result["content"]
    assert isinstance(content, list)
    return str(content[0]["text"])


def launched_on(rig: DeviceRig) -> list[str]:
    return [argv[2] for argv in rig.argv() if argv[:2] == ("simctl", "launch")]


async def test_one_agent_choosing_a_device_leaves_another_agent_of_its_scope_on_its_own_and_running(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    assert (await runtime.call(B, "sim_snapshot", {}))["isError"] is False
    theirs = runtime.manager.instance(B.scope)
    assert theirs is not None and runtime.selections.of(B) == BOOTED_UDID, "bound at its first call"
    rig.clock.now += 10
    first = await runtime.call(A, "sim_snapshot", {})
    assert said(first).startswith(
        "note: Claude Code #bbbb works on this device (last call 10 s ago); sim_device list shows free ones\n"
    ), "told before anything else, who works on the device it was given"
    chose = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX})
    assert chose["isError"] is False and said(chose).startswith("now using iPhone 17 Pro Max")
    assert said(chose).endswith("\nfor you alone; other agents keep theirs")
    assert runtime.manager.is_current(theirs) and theirs.state == "ready", "B's device is not ended"
    assert runtime.manager.instance(B.scope) is theirs and rig.memory.assigned(B.scope, False) == BOOTED_UDID
    assert runtime.selections.of(A) == PRO_MAX and runtime.selections.of(B) == BOOTED_UDID
    assert (await runtime.call(B, "sim_app", LAUNCH))["isError"] is False
    assert (await runtime.call(A, "sim_app", LAUNCH))["isError"] is False
    assert launched_on(rig) == [BOOTED_UDID, PRO_MAX], "each launches on its own device"
    again = await runtime.call(B, "sim_snapshot", {})
    assert not said(again).startswith("note:"), "nobody else works on B's device now"
    stats = await runtime.call(A, "sim_device", {"action": "info"})
    assert said(stats).startswith("iPhone 17 Pro Max") and "\nagent: Claude Code #aaaa\n" in said(stats)
    listed = said(await runtime.call(A, "sim_device", {"action": "list"}))
    assert "udid " + PRO_MAX + " · in use here" in listed
    assert f"udid {BOOTED_UDID} · used by Claude Code #bbbb, last call 0 s ago · the project's own device" in listed


async def test_an_agent_alone_in_its_scope_moves_its_device_as_before_and_a_person_s_pick_moves_it_too(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(A, "sim_snapshot", {})
    old = runtime.manager.instance(A.scope)
    chose = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX})
    mine = runtime.manager.instance(A.scope)
    assert mine is not None and said(chose) == "now using " + device_line(mine), "said as it always was"
    assert "agent:" not in said(await runtime.call(A, "sim_device", {}))
    assert old is not None and old.state == "stopped", "the device it left is let go, as it always was"
    assert rig.memory.assigned(A.scope, False) == PRO_MAX and runtime.manager.instance(A.scope) is not None
    await runtime.manager.choose(A.scope, BOOTED_UDID)
    assert runtime.selections.of(A) == BOOTED_UDID, "a person's pick moves the agents on the old device with it"


async def test_an_agent_may_make_the_device_it_chooses_the_scopes_own_and_its_old_one_is_let_go_when_unused(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(B, "sim_snapshot", {})
    await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX, "steal": True})
    mine = runtime.manager.instance(A.scope, PRO_MAX)
    assert mine is not None
    other = fixture_udid("iPhone 17e")
    made = await runtime.call(A, "sim_device", {"action": "choose", "udid": other, "default": True})
    assert said(made).endswith("\nand the project's own device too")
    assert rig.memory.assigned(A.scope, False) == other and mine.state == "stopped", "nobody else worked on it"
    theirs = runtime.manager.instance(B.scope, BOOTED_UDID)
    assert theirs is not None and theirs.state == "ready" and runtime.selections.of(B) == BOOTED_UDID
    refused = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX, "default": "yes"})
    assert refused["isError"] is True and said(refused) == "default is true or false"
    same = await runtime.call(A, "sim_device", {"action": "choose", "udid": other})
    assert said(same).startswith("already using iPhone 17e")
    nowhere = await runtime.call(A, "sim_device", {"action": "choose", "udid": "00000000-0000-0000-0000-000000000001"})
    assert nowhere["isError"] is True and "simulator" in said(nowhere).lower()
    mine = runtime.manager.instance(A.scope, other)
    assert mine is not None
    mine.busy = "running tests (b1)"
    busy = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX})
    assert said(busy) == "the device is busy: running tests (b1)"


async def test_a_free_device_is_given_to_an_agent_that_chose_none_while_another_works_on_the_scopes_own(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(agent_default="free")
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    alone = await runtime.call(B, "sim_snapshot", {})
    assert not said(alone).startswith("note:") and runtime.selections.of(B) == BOOTED_UDID, "alone: the scope's own"
    given = await runtime.call(A, "sim_snapshot", {})
    assert said(given).startswith(
        "note: you were given iPhone 17 Pro Max, a device nobody uses: Claude Code #bbbb works on this project's own "
        "(device.agent_default)\n"
    )
    assert runtime.selections.of(A) == PRO_MAX and runtime.manager.instance(A.scope, PRO_MAX) is not None
    third = Caller(scope("tp-1"), key="agent-c", title="Codex #cccc")
    listed = said(await runtime.call(third, "sim_device", {"action": "list"}))
    assert runtime.selections.of(third) is None and "used by Claude Code #aaaa" in listed, "a list gives nothing"


async def test_a_free_device_is_not_given_when_none_is_free_or_none_can_be_listed(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(agent_default="free")
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(B, "sim_snapshot", {})
    for entries in rig.devices["devices"].values():
        entries[:] = [entry for entry in entries if entry["udid"] == BOOTED_UDID]
    shared = await runtime.call(A, "sim_snapshot", {})
    assert said(shared).startswith("note: Claude Code #bbbb works on this device") and (
        runtime.selections.of(A) == BOOTED_UDID
    )
    runtime.selections.forget("agent-a")
    rig.config.set(enabled=False)
    assert await runtime._settle(A) == [], "a scope that can have no device lists none"


async def test_an_agents_connection_ending_forgets_its_device_and_the_device_idles_out_for_the_rest(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(B, "sim_snapshot", {})
    await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX, "steal": True})
    mine = runtime.manager.instance(A.scope, PRO_MAX)
    assert mine is not None

    class Leased:
        def in_use(self, device: object) -> bool:
            return "tp-1" in getattr(device, "scopes", ())

    runtime.manager.usage = Leased()
    assert runtime.manager.in_use(mine), "held for the agent working on it"
    runtime.let_go("agent-a")
    assert runtime.selections.of(A) is None and not runtime.manager.in_use(mine), "the scope's lease no longer holds it"
    theirs = runtime.manager.instance(B.scope)
    assert theirs is not None and runtime.manager.in_use(theirs), "the scope's own is still held"


async def test_an_agents_working_is_shown_on_its_own_device(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(B, "sim_snapshot", {})
    await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX, "steal": True})
    mine, theirs = runtime.manager.instance(A.scope, PRO_MAX), runtime.manager.instance(A.scope)
    assert mine is not None and theirs is not None and mine is not theirs
    on_mine, on_theirs = mine.events.subscribe(), theirs.events.subscribe()
    await runtime.call(A, "sim_device", {"action": "info"})
    agents = [event for _ in range(on_mine.qsize()) if (event := on_mine.get_nowait())["type"] == "agent"]
    assert agents and agents[0]["agent"]["key"] == "agent-a"
    theirs_said = [event for _ in range(on_theirs.qsize()) if (event := on_theirs.get_nowait())["type"] == "agent"]
    assert theirs_said == []


async def test_with_selection_by_scope_every_agent_shares_its_scopes_device_and_a_choose_moves_them_all(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_selection="scope")
    runtime = runtime_over(rig)
    rig.memory.choose(A.scope, False, BOOTED_UDID)
    await runtime.call(B, "sim_snapshot", {})
    theirs = runtime.manager.instance(B.scope)
    rig.clock.now += 200
    looked = await runtime.call(A, "sim_snapshot", {})
    assert not said(looked).startswith("note:") and runtime.selections.of(A) is None, "nobody is bound"
    chose = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX})
    mine = runtime.manager.instance(A.scope)
    assert mine is not None and said(chose) == "now using " + device_line(mine)
    assert theirs is not None and theirs.state == "stopped" and rig.memory.assigned(A.scope, False) == PRO_MAX
    refused = await runtime.call(B, "sim_app", LAUNCH)
    assert refused["isError"] is True and "is held by Claude Code #aaaa" in said(refused), "A drives what it chose"
    await runtime.call(B, "sim_app", {**LAUNCH, "steal": True})
    assert launched_on(rig) == [PRO_MAX], "B follows the scope's device"


async def test_choosing_a_device_never_ends_the_one_a_build_is_running_on(tmp_path: Path) -> None:
    for selection in ("agent", "scope"):
        rig = DeviceRig(tmp_path / selection)
        rig.config.set(device_selection=selection)
        runtime = runtime_over(rig)
        rig.memory.choose(A.scope, False, BOOTED_UDID)
        await runtime.call(A, "sim_snapshot", {})
        building = runtime.manager.instance(A.scope)
        assert building is not None
        runtime.builds.runs = lambda: [SimpleNamespace(udid=BOOTED_UDID)]  # type: ignore[method-assign,misc]
        chose = await runtime.call(A, "sim_device", {"action": "choose", "udid": PRO_MAX})
        assert chose["isError"] is False, selection
        assert runtime.manager.is_current(building) and building.state == "ready", selection
        assert rig.memory.assigned(A.scope, False) == PRO_MAX, selection
