# SPDX-License-Identifier: Apache-2.0
"""One agent drives a device at a time: another agent's call that would change a real device is refused for two minutes
after the driver last acted, unless it steals -- while looking is never refused, and a simulator only says another
agent also drove it."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from sim_mirror.core.actions import AgentActions
from sim_mirror.core.drivers import HOLD_S, Drivers, Held, changes_device, exclusive
from sim_mirror.core.instance import Driver
from sim_mirror.core.runtime import Runtime
from sim_mirror.host_copy import HostCopy
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakePhoneBackend, no_wait
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.testing.vision import FakeTextRecognizer
from sim_mirror.tools.results import Result

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code · checkout")
OTHER = Caller(scope("tp-1"), key="agent-2", title="Codex · checkout")
TAP = {"steps": [{"tap": [10, 10]}], "snapshot": "none"}
LAUNCH = {"action": "launch", "bundle_id": "com.example.Notes"}


def runtime_over(rig: DeviceRig) -> Runtime:
    """A runtime over the rig's own devices -- its fake phone among them."""
    runtime = Runtime.build(
        config=rig.config,
        state=rig.state,
        memory=rig.memory,
        policy=rig.policy,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        vision=FakeTextRecognizer(),
        clock=rig.clock,
        sleep=no_wait,
        platform="darwin",
    )
    actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait)
    return dataclasses.replace(runtime, manager=rig.manager, actions=actions)


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


def phone_rig(tmp_path: Path) -> DeviceRig:
    return DeviceRig(tmp_path, phones=FakePhoneBackend(), phone=FakeConnector("phone", kinds=frozenset({"physical"})))


async def test_a_second_agent_is_refused_a_real_device_for_two_minutes_takes_it_with_steal_and_after_them(
    tmp_path: Path,
) -> None:
    rig = phone_rig(tmp_path)
    runtime = runtime_over(rig)
    await rig.manager.choose(CALLER.scope, PHONE_UDID)
    first = await runtime.call(CALLER, "sim_act", TAP)
    assert first["isError"] is False and said(first) == "ok tap (10,10) +0ms", "the first agent drives it"
    rig.clock.now += 60
    refused = await runtime.call(OTHER, "sim_act", TAP)
    assert refused["isError"] is True
    assert re.fullmatch(
        r"Test iPhone is held by Claude Code · checkout since \d\d:\d\d:\d\d \(its last call 60 s ago\); pass steal: "
        r"true to take it, or call again once it has been left alone for 2 minutes \(device\.exclusive, "
        r"`sim-mirror config`\)\.",
        said(refused),
    ), said(refused)
    assert (await runtime.call(OTHER, "sim_app", LAUNCH))["isError"] is True, "launching an app changes it too"
    # Looking is never refused.
    for name, arguments in (("sim_snapshot", {}), ("sim_screenshot", {}), ("sim_device", {"action": "info"})):
        assert (await runtime.call(OTHER, name, arguments))["isError"] is False, name
    stolen = await runtime.call(OTHER, "sim_act", {**TAP, "steal": True})
    assert stolen["isError"] is False
    assert said(stolen) == "ok tap (10,10) +0ms\nnote: Claude Code · checkout also drove this device 60 s ago"
    assert "held by Codex · checkout" in said(await runtime.call(CALLER, "sim_act", TAP))
    # Two quiet minutes later it passes over, saying nothing.
    rig.clock.now += HOLD_S
    after = await runtime.call(CALLER, "sim_act", TAP)
    assert after["isError"] is False and said(after) == "ok tap (10,10) +0ms"
    assert (await runtime.call(CALLER, "sim_act", TAP))["isError"] is False, "its own calls are never refused"


async def test_a_call_refused_before_it_brings_the_device_up_drives_nothing(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    refused = await runtime.call(CALLER, "sim_act", {**TAP, "snapshot": "some"})
    assert refused["isError"] is True and rig.manager.instance(CALLER.scope) is None


async def test_a_simulator_only_says_another_agent_also_drove_it_unless_every_device_is_exclusive(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    await runtime.call(CALLER, "sim_act", TAP)
    rig.clock.now += 20
    also = await runtime.call(OTHER, "sim_app", LAUNCH)
    assert also["isError"] is False
    assert said(also).endswith("\nnote: Claude Code · checkout also drove this device 20 s ago")
    rig.config.set(device_exclusive="all")
    assert "is held by Codex · checkout" in said(await runtime.call(CALLER, "sim_act", TAP))
    logs = await runtime.call(CALLER, "sim_app", {"action": "logs"})
    assert "held by" not in said(logs), "reading the log changes nothing"
    assert await runtime.call(CALLER, "sim_act", {**TAP, "steal": "yes"}) == {
        "content": [{"type": "text", "text": "steal is true or false"}],
        "isError": True,
    }


async def test_a_real_device_is_shared_freely_with_exclusive_off(tmp_path: Path) -> None:
    rig = phone_rig(tmp_path)
    rig.config.set(device_exclusive="off")
    runtime = runtime_over(rig)
    await rig.manager.choose(CALLER.scope, PHONE_UDID)
    await runtime.call(CALLER, "sim_act", TAP)
    shared = await runtime.call(OTHER, "sim_act", TAP)
    assert shared["isError"] is False and said(shared).endswith("also drove this device 0 s ago")


def test_which_calls_change_the_device_and_where_one_agent_drives_at_a_time() -> None:
    assert changes_device("sim_act", {"steps": []})
    assert changes_device("sim_app", {"action": "install"}) and changes_device("sim_app", {"action": "open_url"})
    assert not changes_device("sim_app", {"action": "info"}) and not changes_device("sim_app", {"action": "logs"})
    assert not changes_device("sim_snapshot", {}) and not changes_device(None, {})
    assert not changes_device("sim_act", "tap"), "arguments that are no object are refused by the tool"
    assert exclusive("real_devices", real=True) and not exclusive("real_devices", real=False)
    assert exclusive("all", real=False) and not exclusive("off", real=True)


async def test_a_call_that_ends_keeps_its_agent_driving_and_leaves_a_device_another_took_meanwhile(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    drivers = Drivers(HostCopy(), clock=rig.clock, wall=lambda: 0.0)
    drivers.done(CALLER, instance)
    assert instance.driver == Driver("tp-1", "agent-1", CALLER.title, rig.clock.now, 0.0, rig.clock.now)
    rig.clock.now += 90
    drivers.done(CALLER, instance)
    assert instance.driver.last_call == rig.clock.now, "a long call's end counts: the device is not passed over"
    assert drivers.take(OTHER, instance, exclusive=False, steal=False).startswith("Claude Code · checkout also")
    drivers.done(CALLER, instance)
    assert instance.driver.key == "agent-2"


def test_the_words_for_a_held_device_say_who_since_when_and_how_to_take_it() -> None:
    held = HostCopy(settings="Settings › Devices").device_held("iPhone A", "Codex", "14:03:12", 40)
    assert held.startswith("iPhone A is held by Codex since 14:03:12 (its last call 40 s ago); pass steal: true")
    assert held.endswith("(device.exclusive, Settings › Devices).")
    assert HostCopy().also_drove("Codex", 12) == "Codex also drove this device 12 s ago"


def test_building_testing_and_setting_the_device_change_it_too() -> None:
    assert changes_device("sim_build_run", {}) and changes_device("sim_test", {"scheme": "App"})
    for action in ("boot", "restart", "stop", "appearance", "location", "rotate", "text_size"):
        assert changes_device("sim_device", {"action": action}), action
    for action in ("info", "list", "choose", "health"):
        assert not changes_device("sim_device", {"action": action}), action


async def test_a_device_stays_held_through_a_call_that_runs_past_two_minutes(tmp_path: Path) -> None:
    rig = phone_rig(tmp_path)
    await rig.manager.choose(CALLER.scope, PHONE_UDID)
    instance = await rig.manager.ensure(CALLER.scope)
    drivers = Drivers(HostCopy(), clock=rig.clock, wall=lambda: 0.0)
    assert drivers.take(CALLER, instance, exclusive=True, steal=False) == ""
    rig.clock.now += HOLD_S + 60
    # A long wait is still under way: its device is not another agent's to take quietly.
    assert instance.driver is not None and instance.driver.calls == 1
    with pytest.raises(Held):
        drivers.take(OTHER, instance, exclusive=True, steal=False)
    drivers.done(CALLER, instance)
    assert instance.driver.calls == 0
    rig.clock.now += HOLD_S
    assert drivers.take(OTHER, instance, exclusive=True, steal=False) == "", "two quiet minutes after it ended"


async def test_a_device_is_let_go_at_once_when_its_agent_s_connection_ends(tmp_path: Path) -> None:
    rig = phone_rig(tmp_path)
    runtime = runtime_over(rig)
    await rig.manager.choose(CALLER.scope, PHONE_UDID)
    assert (await runtime.call(CALLER, "sim_act", TAP))["isError"] is False
    assert (await runtime.call(OTHER, "sim_act", TAP))["isError"] is True
    assert runtime.drivers.let_go("agent-1", rig.manager.instances()) == [PHONE_UDID]
    assert runtime.drivers.let_go("agent-1", rig.manager.instances()) == [], "let go once"
    assert said(await runtime.call(OTHER, "sim_act", TAP)) == "ok tap (10,10) +0ms", "an agent started again drives"


async def test_steal_is_taken_by_every_tool_that_changes_the_device(tmp_path: Path) -> None:
    rig = phone_rig(tmp_path)
    runtime = runtime_over(rig)
    await rig.manager.choose(CALLER.scope, PHONE_UDID)
    await runtime.call(CALLER, "sim_act", TAP)
    held = await runtime.call(OTHER, "sim_device", {"action": "appearance", "mode": "dark"})
    assert held["isError"] is True and "held by" in said(held)
    stolen = await runtime.call(OTHER, "sim_device", {"action": "appearance", "mode": "dark", "steal": True})
    assert "held by" not in said(stolen) and "also drove this device" in said(stolen)
