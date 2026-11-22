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
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakePhoneBackend, fixture_udid, no_wait
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
        r"Test iPhone is held by Claude Code · checkout since \d\d:\d\d:\d\d \(its last call 60 s ago\); "
        r"sim_device list shows the devices nobody uses, and choose takes one -- or pass steal: true to take this one, "
        r"or call again once it has been left alone for 2 minutes \(device\.exclusive, `sim-mirror config`\)\.",
        said(refused),
    ), said(refused)
    assert (await runtime.call(OTHER, "sim_app", LAUNCH))["isError"] is True, "launching an app changes it too"
    # Looking is never refused.
    for name, arguments in (("sim_snapshot", {}), ("sim_screenshot", {}), ("sim_device", {"action": "info"})):
        assert (await runtime.call(OTHER, name, arguments))["isError"] is False, name
    stolen = await runtime.call(OTHER, "sim_act", {**TAP, "steal": True})
    assert stolen["isError"] is False
    assert said(stolen) == "note: Claude Code · checkout also drove this device 60 s ago\nok tap (10,10) +0ms"
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


async def test_a_simulator_only_says_another_agent_also_drove_it_where_only_real_devices_are_exclusive(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_exclusive="real_devices")
    runtime = runtime_over(rig)
    await runtime.call(CALLER, "sim_act", TAP)
    rig.clock.now += 20
    also = await runtime.call(OTHER, "sim_app", LAUNCH)
    assert also["isError"] is False
    assert said(also).startswith("note: Claude Code · checkout also drove this device 20 s ago\n")
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
    assert shared["isError"] is False and said(shared).startswith(
        "note: Claude Code · checkout also drove this device 0 s"
    )


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
    assert held.startswith("iPhone A is held by Codex since 14:03:12 (its last call 40 s ago); sim_device list shows")
    assert "pass steal: true to take this one" in held
    assert held.endswith("(device.exclusive, Settings › Devices).")
    assert HostCopy().also_drove("Codex", 12) == "Codex also drove this device 12 s ago"


def test_building_testing_and_setting_the_device_change_it_too() -> None:
    assert changes_device("sim_build_run", {}) and changes_device("sim_test", {"scheme": "App"})
    for action in ("choose", "boot", "restart", "stop", "appearance", "location", "rotate", "text_size"):
        assert changes_device("sim_device", {"action": action}), action
    assert changes_device("sim_record", {"action": "start"}) and changes_device("sim_record", {"action": "stop"})
    assert not changes_device("sim_record", {"action": "status"})
    for action in ("info", "list", "health"):
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
    assert runtime.let_go("agent-1") == [PHONE_UDID]
    assert runtime.let_go("agent-1") == [], "let go once"
    assert runtime.selections.of(CALLER) is None, "and its binding forgotten"
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


# -- told before acting, by name ------------------------------------------------------------------------------------

A = Caller(scope("tp-1"), key="agent-a", title="Claude Code #aaaa")
B = Caller(scope("tp-1"), key="agent-b", title="Claude Code · ticket-123")


async def test_every_device_is_exclusive_by_default_and_an_agent_is_refused_up_front_on_one_another_works_on(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    assert rig.config.get(A.scope).device_exclusive == "all"
    await runtime.call(B, "sim_snapshot", {})
    rig.clock.now += HOLD_S + 30
    taps = len(rig.idb.engine.hid_events)
    refused = await runtime.call(A, "sim_act", TAP)
    assert refused["isError"] is True and said(refused) == (
        "SimMirror · alpha · tp-1 is in use by Claude Code · ticket-123 (its last call 150 s ago); sim_device list "
        "shows the devices nobody uses, and choose takes one -- or pass steal: true to take this one "
        "(device.exclusive, `sim-mirror config`)."
    )
    assert len(rig.idb.engine.hid_events) == taps, "refused before anything was played"
    instance = rig.manager.instance(A.scope)
    assert instance is not None and instance.driver is None
    started = await runtime.call(A, "sim_record", {"action": "start"})
    assert started["isError"] is True and "is in use by Claude Code · ticket-123" in said(started)
    looked = await runtime.call(A, "sim_screenshot", {})
    assert looked["isError"] is False, "looking is never refused"
    stolen = await runtime.call(A, "sim_act", {**TAP, "steal": True})
    assert stolen["isError"] is False and said(stolen) == "ok tap (10,10) +0ms"
    held = await runtime.call(B, "sim_act", TAP)
    assert "is held by Claude Code #aaaa since" in said(held), "the agent that stole it holds it for two minutes"


async def test_an_agent_is_refused_choosing_a_device_another_works_on_or_drives_unless_it_steals(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    await runtime.call(B, "sim_act", TAP)
    theirs = rig.manager.instance(B.scope)
    assert theirs is not None
    other = fixture_udid("iPhone 17e")
    await runtime.call(A, "sim_device", {"action": "choose", "udid": other})
    refused = await runtime.call(A, "sim_device", {"action": "choose", "udid": theirs.udid})
    assert refused["isError"] is True and said(refused).startswith(
        f"{theirs.name} is held by Claude Code · ticket-123 since"
    ), "B drove it within two minutes"
    rig.clock.now += HOLD_S
    unheld = await runtime.call(A, "sim_device", {"action": "choose", "udid": theirs.udid})
    assert said(unheld).startswith(f"{theirs.name} is in use by Claude Code · ticket-123"), "B still works on it"
    stolen = await runtime.call(A, "sim_device", {"action": "choose", "udid": theirs.udid, "steal": True})
    assert stolen["isError"] is False and said(stolen).startswith(
        "note: Claude Code · ticket-123 works on this device (last call 120 s ago); sim_device list shows free ones\n"
        "now using "
    )
    assert runtime.selections.of(A) == theirs.udid and rig.manager.is_current(theirs)
    rig.clock.now += 1
    await runtime.call(B, "sim_act", TAP)
    driven = await runtime.call(A, "sim_device", {"action": "choose", "udid": other, "steal": True})
    assert driven["isError"] is False
    back = await runtime.call(A, "sim_device", {"action": "choose", "udid": theirs.udid, "steal": True})
    assert said(back).startswith("note: Claude Code · ticket-123 also drove this device 0 s ago\n")
    assert await runtime.call(A, "sim_device", {"action": "choose"}) == {
        "content": [{"type": "text", "text": "choose needs the udid of a device sim_device list shows"}],
        "isError": True,
    }


async def test_where_another_agent_is_not_refused_it_is_told_first_who_works_on_the_device_once_in_two_minutes(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_exclusive="off")
    runtime = runtime_over(rig)
    await runtime.call(B, "sim_snapshot", {})
    rig.clock.now += 5
    note = "note: Claude Code · ticket-123 works on this device (last call 5 s ago); sim_device list shows free ones\n"
    assert said(await runtime.call(A, "sim_act", TAP)).startswith(note + "ok tap")
    assert not said(await runtime.call(A, "sim_snapshot", {})).startswith("note:"), "told once in two minutes"
    assert not said(await runtime.call(A, "sim_device", {"action": "list"})).startswith("note:")
    rig.clock.now += 120
    told = said(await runtime.call(A, "sim_snapshot", {}))
    assert told.startswith("note: Claude Code · ticket-123 works on this device (last call 125 s ago)")
    listed = said(await runtime.call(B, "sim_device", {"action": "info"}))
    assert "\nagent: Claude Code · ticket-123\n" in listed, "it names itself while other agents work too"


async def test_a_device_one_agent_stops_keeps_running_for_another_working_on_it(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_exclusive="off")
    runtime = runtime_over(rig)
    await runtime.call(B, "sim_snapshot", {})
    await runtime.call(A, "sim_snapshot", {})
    instance = rig.manager.instance(A.scope)
    assert instance is not None
    stopped = await runtime.call(A, "sim_device", {"action": "stop", "shutdown": True})
    assert said(stopped) == (
        f"let go of {instance.name}: Claude Code · ticket-123 still works on it, so it keeps running. The next call "
        "that needs a device starts yours."
    )
    assert rig.manager.is_current(instance) and runtime.selections.of(A) is None
    assert ("simctl", "shutdown", instance.udid) not in rig.argv()


async def test_a_call_on_a_device_that_is_not_running_yet_is_refused_for_the_agent_working_on_it(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    runtime = runtime_over(rig)
    other = fixture_udid("iPhone 17e")
    runtime.selections.begin(B)
    runtime.selections.bind(B, other)
    runtime.selections.end(B)
    rig.clock.now += 1
    runtime.selections.begin(A)
    runtime.selections.bind(A, other)
    runtime.selections.end(A)
    refused = await runtime.call(A, "sim_app", LAUNCH)
    assert said(refused).startswith("this device is in use by Claude Code · ticket-123"), "nothing runs to name"
