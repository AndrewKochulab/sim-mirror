# SPDX-License-Identifier: Apache-2.0
"""The composition root: SimMirror put together from seams, started and closed, told of changed settings, and the one
answer to whether a scope's agents may use their tools now."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.core.runtime import Runtime
from sim_mirror.core.screen_relay import ScreenRelay
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import FakeConnector, FakeProcess, FakeXcrun, MemoryStateStore, StaticConfig, no_wait
from sim_mirror.testing.rig import DeviceRig, scope

CALLER = Caller(scope("tp-1"), key="agent-1", title="Codex")


class Started:
    """A build runner whose xcodebuild runs until a test ends it, and whose ending signals nothing real."""

    def __init__(self, rig: DeviceRig) -> None:
        self.processes: list[FakeProcess] = []
        self.runner = BuildRunner(rig.state, xcrun=rig.xcrun, start=self.start, signal_group=self.signal)

    async def start(self, *args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> FakeProcess:
        process = FakeProcess(6000 + len(self.processes))
        self.processes.append(process)
        return process

    def signal(self, pid: int, sig: int) -> None:
        next(process for process in self.processes if process.pid == pid).finish(-sig)


def runtime_over(rig: DeviceRig, builds: BuildRunner | None = None) -> Runtime:
    return Runtime.build(
        config=rig.config,
        state=rig.state,
        policy=rig.policy,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        builds=builds,
        xcrun=rig.xcrun,
        clock=rig.clock,
        sleep=no_wait,
    )


def test_a_runtime_without_a_registry_finds_the_built_in_connectors(tmp_path: Path) -> None:
    runtime = Runtime.build(config=StaticConfig(), state=MemoryStateStore(tmp_path), policy=DeviceRig(tmp_path).policy)
    assert {"idb", "simctl"} <= set(runtime.registry.names())
    assert runtime.tools.names()[0] == "sim_device" and runtime.builds.runs() == []


async def test_starting_ends_what_an_earlier_run_left_and_closing_stops_everything(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    await runtime.start()
    assert rig.idb.reaped == 1 and runtime.reaper.running
    instance = await runtime.manager.ensure(CALLER.scope)
    assert instance.task is not None
    await instance.task
    await runtime.close()
    assert not runtime.reaper.running and rig.idb.closed == [instance.udid]


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
    ]
    said = await runtime.call(CALLER, "sim_device", {})
    assert said["content"][0]["text"] == "No simulator is running here; sim_device boot starts one."
    rig.config.set(agent_tools=False)
    off = "The iOS Simulator's agent tools are off for this project (`sim-mirror config`)."
    assert await runtime.manifest(CALLER.scope) == {"tools": [], "instructions": off}
    assert (await runtime.call(CALLER, "sim_device", {}))["isError"] is True
    rig.config.set(agent_tools=True, enabled=False)
    refused = await runtime.call(CALLER, "sim_device", {})
    assert refused["content"][0]["text"] == "The iOS Simulator is off for this project (`sim-mirror config`)."


async def test_a_view_only_mirror_offers_its_agents_no_touching(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False))
    names = [tool["name"] for tool in (await runtime_over(rig).manifest(CALLER.scope))["tools"]]
    assert names == ["sim_device", "sim_screenshot", "sim_app"]


def test_a_call_runs_with_the_hosts_policy_as_it_is_now(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    rig.policy.roots, rig.policy.folder, rig.policy.shells = (tmp_path,), tmp_path / "app", True
    ctx = runtime.tool_context(CALLER)
    assert (ctx.roots, ctx.folder, ctx.shells_allowed) == ((tmp_path,), tmp_path / "app", True)
    assert ctx.builds is runtime.builds and ctx.caller is CALLER and ctx.copy is rig.copy


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
