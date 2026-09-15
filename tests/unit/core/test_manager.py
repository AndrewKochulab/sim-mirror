# SPDX-License-Identifier: Apache-2.0
"""The device manager: a device brought up in the background, claimed, shared, bounded, on the connector that can
reach it, and ended when off, idle or asked."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, Shot
from sim_mirror.core.availability import ONLY_ON_A_MAC
from sim_mirror.core.frames import StreamSettings
from sim_mirror.core.instance import BOOTING, FAILED, READY, STOPPED
from sim_mirror.core.manager import BOOT_TIMEOUT_S, RESTART_S, SimulatorUnavailable
from sim_mirror.protocol import CLOSE_FORBIDDEN, CLOSE_RESTARTING, CLOSE_STOPPED
from sim_mirror.storage.claims import Claims
from sim_mirror.testing.fakes import BOOTED_UDID, JPEG, SCREEN, FakeConnector, FakeEngine, fixture_udid, made
from sim_mirror.testing.rig import DeviceRig, closer_log, scope, settle

SHUTDOWN_UDID = fixture_udid("iPhone 17 Pro Max")
TP1 = scope("tp-1")


async def test_why_a_scope_cannot_have_a_simulator(tmp_path: Path) -> None:
    assert await DeviceRig(tmp_path, platform="linux").manager.unavailable(TP1) == ONLY_ON_A_MAC
    rig = DeviceRig(tmp_path)
    assert await rig.manager.unavailable(TP1) is None
    rig.policy.area = False
    assert await rig.manager.unavailable(TP1) == "The iOS Simulator is not available here."
    rig.policy.area = True
    rig.config.set(enabled=False)
    assert await rig.manager.unavailable(TP1) == "The iOS Simulator is off for this project (`sim-mirror config`)."
    rig.config.set(enabled=True)
    rig.idb.available = rig.simctl_connector.available = False
    assert (await rig.manager.unavailable(TP1) or "").startswith("No connector can reach a simulator here.")


async def test_a_scope_gets_its_device_at_once_and_it_is_brought_up_in_the_background(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", hold=True))
    instance = await rig.manager.ensure(TP1)
    assert (instance.state, instance.udid, instance.created, instance.name) == (
        BOOTING,
        made(1),
        True,
        "SimMirror · alpha · tp-1",
    )
    assert instance.session is None and instance.connector == "idb" and instance.fallback_reason is None
    events = instance.events.subscribe()
    assert rig.manager.instance(TP1) is instance and rig.manager.instances() == [instance]
    status = await rig.manager.status(TP1)
    assert (
        status["device"] is not None and status["device"]["state"] == "booting" and status["device"]["screen"] is None
    )
    assert status["reason"] is None and status["connector"] == "idb" and "element_tree" in status["capabilities"]
    assert status["stream"] == {"encoding": "auto", "fps": 30} and status["cursor"] == {"enabled": True, "lead_ms": 250}
    assert rig.idb.release is not None
    rig.idb.release.set()
    assert instance.task is not None
    await instance.task
    assert instance.state == READY and instance.screen == SCREEN and instance.hub is not None
    assert instance.session is not None and instance.session.screen is rig.idb.engine
    assert events.get_nowait()["state"] == "ready"
    assert instance.booted_by_us and ("simctl", "boot", made(1)) in rig.argv()
    device = (await rig.manager.status(TP1))["device"]
    assert device is not None and device["screen"] == {"points": {"w": 402, "h": 874},
                                                       "pixels": {"w": 1206, "h": 2622}, "scale": 3.0}  # fmt: skip
    bootstatus = next(call for call in rig.xcrun.calls if call.args[1] == "bootstatus")
    assert bootstatus.args == ("simctl", "bootstatus", made(1), "-b") and bootstatus.timeout == BOOT_TIMEOUT_S
    assert rig.idb.attached == [made(1)]
    rig.clock.now += 30
    assert await rig.manager.ensure(TP1) is instance and instance.last_used == rig.clock.now
    unknown = await rig.manager.status(scope("tp-9"))
    assert unknown["device"] is None and unknown["connector"] == "idb" and unknown["fallback_reason"] is None


async def test_without_idb_a_device_is_mirrored_by_simctl_and_says_why(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False, reasons=("no idb_companion.",)))
    before = await rig.manager.status(TP1)
    assert before["connector"] == "simctl" and before["fallback_reason"] == "no idb_companion."
    assert "input_touch" not in before["capabilities"] and before["reason"] is None
    instance = await rig.up()
    assert instance.connector == "simctl" and instance.fallback_reason == "no idb_companion."
    assert Capability.INPUT_TOUCH not in instance.capabilities
    assert instance.hub is not None and instance.hub.settings == StreamSettings(4, 75, 900)
    status = await rig.manager.status(TP1)
    assert status["connector"] == "simctl" and status["fallback_reason"] == "no idb_companion."


async def test_a_device_that_is_off_is_booted_and_one_already_on_is_left_as_it_was(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.manager.directory.memory.choose(scope("tp-1"), False, SHUTDOWN_UDID)
    rig.manager.directory.memory.choose(scope("tp-2"), False, BOOTED_UDID)
    off, on = await rig.up("tp-1"), await rig.up("tp-2")
    assert off.booted_by_us and ("simctl", "boot", SHUTDOWN_UDID) in rig.argv() and not off.created
    assert not on.booted_by_us and ("simctl", "boot", BOOTED_UDID) not in rig.argv()


async def test_a_restarting_stop_tells_its_screens_the_device_will_be_back(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    closed, close = closer_log()
    rig.manager.attach(instance, close)
    assert await rig.manager.stop(TP1, shutdown_device=True, restarting=True) is True
    assert closed == [(CLOSE_RESTARTING, "the simulator is restarting")] and instance.state == STOPPED
    assert ("simctl", "shutdown", made(1)) in rig.argv() and rig.idb.closed == [made(1)]


async def test_a_device_simmirror_made_is_shut_down_when_idle_even_if_an_earlier_run_booted_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(idle_minutes=5)
    rig.manager.directory.memory.choose(scope("tp-2"), False, BOOTED_UDID)
    made_here, persons = await rig.up("tp-1"), await rig.up("tp-2")
    made_here.booted_by_us = False
    assert made_here.created and made_here.may_shut_down
    assert not persons.created and not persons.booted_by_us and not persons.may_shut_down
    rig.clock.now += 5 * 60
    assert sorted(await rig.manager.reap()) == sorted([made_here.udid, persons.udid])
    assert ("simctl", "shutdown", made_here.udid) in rig.argv()
    assert ("simctl", "shutdown", BOOTED_UDID) not in rig.argv()


async def test_a_device_simctl_no_longer_lists_is_not_booted_and_boot_status_decides(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, list_created=False)
    instance = await rig.up()
    assert instance.state == READY and not instance.booted_by_us
    assert not any(args[1] == "boot" for args in rig.argv())


async def test_a_shared_group_puts_every_scope_on_one_device_until_the_last_lets_go(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_mode="shared")
    first = await rig.up("tp-1")
    second = await rig.manager.ensure(scope("tp-2"))
    assert second is first and first.scopes == {"tp-1", "tp-2"} and first.name == "SimMirror · alpha"
    assert await rig.manager.stop(scope("tp-1")) is True
    assert first.state == READY and rig.manager.instance(scope("tp-2")) is first
    assert await rig.manager.stop(scope("tp-2")) is True and first.state == STOPPED
    assert await rig.manager.stop(scope("tp-2")) is False


async def test_refusals_say_why_with_the_status_they_mean(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)

    async def refused() -> tuple[int, str]:
        with pytest.raises(SimulatorUnavailable) as caught:
            await rig.manager.ensure(TP1)
        return caught.value.status, str(caught.value)

    rig.config.set(enabled=False)
    status, message = await refused()
    assert status == 409 and "is off for this project" in message
    rig.config.set(enabled=True, runtime="iOS 99.0")
    status, message = await refused()
    assert status == 409 and "No iOS 99.0 simulator runtime" in message
    rig.config.set(runtime="")
    rig.xcrun.on("simctl", "list", "runtimes", rc=1, err="CoreSimulatorService connection became invalid")
    status, message = await refused()
    assert status == 502 and "connection became invalid" in message


async def test_a_device_that_cannot_be_brought_up_says_why_and_starts_over_when_asked_again(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", fail=ConnectorUnavailable("idb_companion did not answer", 504)))
    failed = await rig.up()
    assert failed.state == FAILED and failed.reason == "idb_companion did not answer" and failed.live is False
    assert not (tmp_path / "claims" / f"{failed.udid}.json").exists()
    rig.idb.fail = None
    again = await rig.up()
    assert again is not failed and again.state == READY and failed.state == STOPPED


async def test_a_shared_device_that_failed_is_started_over_by_the_next_scope_that_asks(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", fail=ConnectorUnavailable("not now")))
    rig.config.set(device_mode="shared")
    failed = await rig.up("tp-1")
    rig.idb.fail = None
    fresh = await rig.up("tp-2")
    assert fresh is not failed and fresh.state == READY and failed.state == STOPPED


async def test_a_connector_that_cannot_describe_the_screen_is_let_go_again(tmp_path: Path) -> None:
    engine = FakeEngine(describe_error=ConnectorError("describing the device failed: offline"))
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=engine))
    instance = await rig.up()
    assert instance.state == FAILED and "offline" in (instance.reason or "")
    assert rig.idb.closed == [made(1)] and instance.session is None


async def test_something_unexpected_while_starting_is_a_failure_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)

    def explode(args: tuple[str, ...]) -> object:
        raise RuntimeError("boom")

    rig.xcrun.on("simctl", "bootstatus", then=explode)  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        instance = await rig.up()
    assert instance.state == FAILED and instance.reason == "The simulator could not be started: boom"
    assert "could not be started" in caplog.text


async def test_a_device_another_process_is_driving_is_refused_saying_who(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.manager.directory.memory.choose(TP1, False, BOOTED_UDID)
    rig.alive_pids.add(9999)

    async def started(pid: int) -> str | None:
        return f"started-{pid}"

    other = Claims(tmp_path / "claims", owner="Host", pid=9999, pid_alive=lambda pid: True, start_time=started)
    await other.acquire(BOOTED_UDID)
    instance = await rig.up()
    assert instance.state == FAILED and rig.idb.attached == []
    assert instance.reason == (
        "Another Host on this Mac (pid 9999) is already showing this simulator. "
        "Stop it there, or give this SimMirror a different device."
    )
    assert await other.holder(BOOTED_UDID) is None and await rig.claims.holder(BOOTED_UDID) is not None
    await other.release(BOOTED_UDID)
    again = await rig.up()
    assert again.state == READY and await other.holder(BOOTED_UDID) is not None
    await rig.manager.stop(TP1)
    assert await other.holder(BOOTED_UDID) is None


async def test_max_booted_ends_the_least_recently_used_idle_device_or_refuses(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(max_booted=1)
    rig.manager.directory.memory.choose(scope("tp-1"), False, SHUTDOWN_UDID)
    first = await rig.up("tp-1")
    rig.clock.now += 5
    second = await rig.up("tp-2")
    assert first.state == STOPPED and ("simctl", "shutdown", SHUTDOWN_UDID) in rig.argv() and second.state == READY
    _closed, close = closer_log()

    class Holding:
        def __init__(self, held: bool) -> None:
            self.held = held

        def in_use(self, device: object) -> bool:
            return self.held

    def by_viewer() -> None:
        rig.manager.attach(second, close)

    def by_build() -> None:
        second.busy = "tests running"

    def by_agent() -> None:
        rig.manager.usage = Holding(True)

    for hold in (by_viewer, by_build, by_agent):
        rig.manager.detach(second, close)
        second.busy = None
        rig.manager.usage = Holding(False)
        hold()
        with pytest.raises(SimulatorUnavailable, match="1 simulator is already running and in use"):
            await rig.manager.ensure(scope("tp-3"))
    assert second.state == READY


async def test_stopping_closes_every_screen_and_shuts_the_device_down_when_asked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    closed, close = closer_log()

    async def broken(code: int, reason: str) -> None:
        raise RuntimeError("socket already gone")

    rig.manager.attach(instance, close)
    rig.manager.attach(instance, broken)
    assert instance.viewers == 2
    rig.xcrun.on("simctl", "shutdown", rc=1, err="Invalid device")
    with caplog.at_level(logging.WARNING):
        assert await rig.manager.stop(TP1, shutdown_device=True) is True
    assert closed == [(CLOSE_STOPPED, "the simulator stopped")] and instance.viewers == 0
    assert rig.idb.closed == [made(1)] and ("simctl", "shutdown", made(1)) in rig.argv()
    assert "could not shut down the simulator" in caplog.text


async def test_stopping_a_device_still_being_brought_up_cancels_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", hold=True))
    instance = await rig.manager.ensure(TP1)
    await settle()
    assert rig.idb.attached and instance.state == BOOTING
    assert await rig.manager.stop(TP1) is True
    assert instance.task is not None and instance.task.cancelled() and instance.state == STOPPED
    assert rig.idb.closed == []


async def test_tickets_open_a_screen_once_and_viewers_keep_a_device_in_use(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    bound = rig.manager.mint_ticket(instance, origin="http://localhost:3000")
    # A scope with no device never reaches the ticket; the right scope from the wrong origin spends it.
    assert rig.manager.consume_ticket(scope("tp-2"), bound) is None
    assert rig.manager.consume_ticket(TP1, bound, origin="http://evil.test") is None
    assert rig.manager.consume_ticket(TP1, bound, origin="http://localhost:3000") is None
    again = rig.manager.mint_ticket(instance, origin="http://localhost:3000")
    assert rig.manager.consume_ticket(TP1, again, origin="http://localhost:3000") is instance
    fresh = rig.manager.mint_ticket(instance)
    assert rig.manager.consume_ticket(TP1, fresh) is instance and rig.manager.consume_ticket(TP1, fresh) is None
    _closed, close = closer_log()
    rig.clock.now += 10
    rig.manager.attach(instance, close)
    assert instance.viewers == 1 and instance.last_used == 110
    rig.clock.now += 10
    rig.manager.touch(instance)
    assert instance.last_used == 120 and rig.manager.now() == 120
    rig.clock.now += 10
    rig.manager.detach(instance, close)
    assert instance.viewers == 0 and instance.last_used == 130
    rig.manager.person_touched(instance)
    assert instance.person_touch_at == 130 and rig.manager.simctl(instance).developer_dir == ""


async def test_changed_settings_apply_at_once_to_that_groups_devices(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    running = await rig.up("tp-1")
    rig.idb.release = asyncio.Event()
    booting = await rig.manager.ensure(scope("tp-2"))
    elsewhere = await rig.manager.ensure(scope("tp-3", group="beta"))
    rig.config.set(stream_fps=12, stream_quality=40)
    await rig.manager.reconcile("beta")
    assert running.hub is not None and running.hub.settings == StreamSettings(30, 75, 900)
    await rig.manager.reconcile("alpha")
    assert running.hub.settings == StreamSettings(12, 40, 900) and booting.hub is None
    closed, close = closer_log()
    rig.manager.attach(running, close)
    rig.config.set(enabled=False)
    await rig.manager.reconcile("alpha")
    off = "The iOS Simulator is off for this project (`sim-mirror config`)."
    assert running.state == STOPPED and booting.state == STOPPED and rig.manager.instances() == [elsewhere]
    assert closed == [(CLOSE_FORBIDDEN, off)] and running.reason == off
    await rig.manager.reconcile()
    assert elsewhere.state == STOPPED


async def test_a_connector_switched_since_brings_the_device_back_on_the_new_one(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    closed, close = closer_log()
    rig.manager.attach(instance, close)
    rig.config.set(connector="simctl")
    await rig.manager.reconcile()
    assert instance.state == STOPPED and closed == [(CLOSE_RESTARTING, "the simulator is restarting")]
    assert ("simctl", "shutdown", made(1)) not in rig.argv()
    mirrored = await rig.up()
    assert mirrored.connector == "simctl" and mirrored.udid == instance.udid


async def test_the_reaper_ends_what_is_off_or_idle_attaches_what_lost_its_connector_and_spares_what_is_used(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(idle_minutes=5)
    rig.manager.directory.memory.choose(scope("tp-2"), False, SHUTDOWN_UDID)
    watched, idle, held, crashed, recent = [await rig.up(t) for t in ("tp-1", "tp-2", "tp-3", "tp-4", "tp-5")]
    _closed, close = closer_log()
    rig.manager.attach(watched, close)

    class HeldOne:
        def in_use(self, device: object) -> bool:
            return device is held

    rig.manager.usage = HeldOne()
    rig.idb.dead.add(crashed.udid)
    rig.clock.now += 5 * 60
    recent.last_used = rig.clock.now - 1
    assert await rig.manager.reap() == [idle.udid]
    assert ("simctl", "shutdown", SHUTDOWN_UDID) in rig.argv()
    assert watched.state == READY and watched.last_used == rig.clock.now and held.state == READY
    assert recent.state == READY
    assert crashed.state == READY and crashed.recovery is not None
    await crashed.recovery
    assert crashed.session is not None and crashed.session.alive and rig.idb.closed.count(crashed.udid) == 1
    assert rig.idb.attached.count(crashed.udid) == 2
    assert await rig.manager.reap() == [crashed.udid]
    rig.config.set(enabled=False)
    assert await rig.manager.reap() == [watched.udid, held.udid, recent.udid]


async def test_a_screen_source_in_trouble_stalls_the_device_until_frames_come_again(tmp_path: Path) -> None:
    engine = FakeEngine()
    engine.screenshot_errors = [ConnectorError("screen went away"), ConnectorError("still away")]
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=engine))
    instance = await rig.up()
    events = instance.events.subscribe()
    assert instance.hub is not None
    viewer = instance.hub.subscribe("jpeg")
    assert await viewer.next() is not None
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert [(event["state"], event["reason"]) for event in seen] == [("stalled", "screen went away"), ("ready", None)]
    await rig.manager.shutdown()


async def test_a_connector_that_stops_is_attached_again_and_its_viewers_keep_watching(tmp_path: Path) -> None:
    first = FakeEngine()
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=first))
    instance = await rig.up()
    assert instance.hub is not None
    viewer = instance.hub.subscribe("jpeg")
    frame = await viewer.next()
    assert frame is not None and frame.data == JPEG
    events = instance.events.subscribe()
    second = FakeEngine()
    second.shot = Shot(b"\xff\xd8second\xff\xd9", 402, 874)
    rig.idb.engine = second
    rig.idb.dead.add(instance.udid)
    first.screenshot_errors = [ConnectorError("the simulator companion did not answer") for _ in range(500)]
    again = await asyncio.wait_for(viewer.next(), 2)
    assert again is not None and again.data == second.shot.jpeg
    assert instance.recovery is not None
    await instance.recovery
    assert instance.session is not None and instance.session.screen is second and instance.state == READY
    assert rig.idb.closed == [instance.udid] and len(rig.idb.attached) == 2
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert [(event["state"], event["reason"]) for event in seen] == [
        ("stalled", "the simulator companion did not answer"),
        ("ready", None),
    ]
    await rig.manager.shutdown()


@pytest.mark.parametrize("breaks", ["attach", "describe", "uninstalled"])
async def test_a_connector_that_will_not_start_again_fails_the_device_saying_why(tmp_path: Path, breaks: str) -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await asyncio.sleep(0)

    rig = DeviceRig(tmp_path, sleep=sleep)
    instance = await rig.up()
    slept.clear()
    rig.idb.dead.add(instance.udid)
    if breaks == "attach":
        rig.idb.fail = ConnectorUnavailable("idb_companion exited as it started (exit 1)")
    elif breaks == "describe":
        rig.idb.engine = FakeEngine(describe_error=ConnectorError("the device is not booted"))
    else:
        instance.connector = "swift-helper"
    assert await rig.manager.reap() == []
    assert instance.recovery is not None
    await instance.recovery
    assert slept == list(RESTART_S) and instance.state == FAILED
    assert (instance.reason or "").startswith("The simulator's connector stopped and could not be started again (")
    expected = {
        "attach": "exit 1",
        "describe": "the device is not booted",
        "uninstalled": "the swift-helper connector is no longer installed",
    }[breaks]
    assert expected in (instance.reason or "")
    attempts = 1 + (len(RESTART_S) if breaks != "uninstalled" else 0)
    assert len(rig.idb.attached) == attempts


async def test_a_restart_under_way_is_not_started_twice_and_a_device_with_no_frame_hub_still_gets_one(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    instance.hub = None
    rig.idb.dead.add(instance.udid)
    await rig.manager.reap()
    under_way = instance.recovery
    await rig.manager.reap()
    assert instance.recovery is under_way and under_way is not None
    await under_way
    assert len(rig.idb.attached) == 2 and instance.session is not None and instance.session.alive
    assert instance.hub is None


async def test_a_device_ended_or_failed_while_its_connector_waits_to_start_again_is_left_alone(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    ended, failed = await rig.up("tp-1"), await rig.up("tp-2")
    for instance in (ended, failed):
        rig.idb.dead.add(instance.udid)
    await rig.manager.reap()
    await rig.manager.stop(scope("tp-1"))
    assert ended.recovery is not None and ended.recovery.cancelled()
    failed.state = FAILED
    assert failed.recovery is not None
    await failed.recovery
    assert rig.idb.attached == [ended.udid, failed.udid]


async def test_the_picker_lists_this_macs_ios_simulators_and_a_pick_is_kept(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    listed = await rig.manager.devices(TP1)
    assert all(device["runtime"].startswith("iOS ") and not device["created"] for device in listed)
    assert [(d["runtime"], d["name"]) for d in listed] == sorted((d["runtime"], d["name"]) for d in listed)
    choice = {"udid": BOOTED_UDID, "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "state": "Booted", "created": False}
    assert choice in listed
    instance = await rig.up()
    mine = {"udid": made(1), "name": "SimMirror · alpha · tp-1", "runtime": "iOS 26.5", "state": "Shutdown",
            "created": True}  # fmt: skip
    assert mine in await rig.manager.devices(TP1)
    await rig.manager.choose(TP1, BOOTED_UDID)
    assert instance.state == STOPPED and rig.manager.directory.memory.assigned(TP1, False) == BOOTED_UDID
    for udid, status in (("00000000-0000-0000-0000-000000000000", 404), ("not-a-udid", 400)):
        with pytest.raises(SimulatorUnavailable) as caught:
            await rig.manager.choose(TP1, udid)
        assert caught.value.status == status
    rig.xcrun.on("simctl", "list", "devices", rc=1, err="boom")
    with pytest.raises(SimulatorUnavailable, match="boom") as refused:
        await rig.manager.devices(TP1)
    assert refused.value.status == 502


async def test_orphans_are_reaped_at_boot_and_shutdown_leaves_devices_running(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)

    class Leftovers(FakeConnector):
        async def reap_orphans(self) -> int:
            return 2

    class Broken(FakeConnector):
        async def reap_orphans(self) -> int:
            raise ConnectorError("cannot list its folder")

    rig.registry._connectors = {"idb": Leftovers("idb"), "simctl": Broken("simctl")}
    assert await rig.manager.start_at_boot() == 2 and "could not clean up" in caplog.text
    rig.registry._connectors = {"idb": rig.idb, "simctl": rig.simctl_connector}
    instance = await rig.up()
    await rig.manager.shutdown()
    assert instance.state == STOPPED and rig.manager.instances() == []
    assert not any(args[1] == "shutdown" for args in rig.argv())


async def test_a_scope_that_cannot_have_a_simulator_lists_and_picks_no_devices(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(enabled=False)
    with pytest.raises(SimulatorUnavailable, match="is off for this project") as listing:
        await rig.manager.devices(TP1)
    with pytest.raises(SimulatorUnavailable, match="is off for this project") as picking:
        await rig.manager.choose(TP1, "any-udid")
    assert listing.value.status == picking.value.status == 409
    assert not any(args[:3] == ("simctl", "list", "devices") for args in rig.argv())


async def test_a_device_ended_while_it_boots_is_shut_down_as_one_simmirror_booted(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.manager.directory.memory.choose(TP1, False, SHUTDOWN_UDID)
    real, booting = rig.xcrun, asyncio.Event()

    async def slow_boot(*args: str, **options: Any) -> Any:
        if args[:2] == ("simctl", "boot"):
            booting.set()
            await asyncio.Event().wait()
        return await real(*args, **options)

    rig.xcrun = slow_boot  # type: ignore[assignment]
    instance = await rig.manager.ensure(TP1)
    await asyncio.wait_for(booting.wait(), 2)
    rig.config.set(enabled=False)
    await rig.manager.reconcile()
    assert instance.state == STOPPED and ("simctl", "shutdown", SHUTDOWN_UDID) in real.argv()


async def test_a_device_simmirror_booted_is_still_its_to_shut_down_after_a_start_that_failed(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", fail=ConnectorUnavailable("not now")))
    rig.manager.directory.memory.choose(TP1, False, SHUTDOWN_UDID)
    failed = await rig.up()
    assert failed.state == FAILED and failed.booted_by_us
    for devices in rig.devices["devices"].values():
        for device in devices:
            if device["udid"] == SHUTDOWN_UDID:
                device["state"] = "Booted"
    rig.idb.fail = None
    again = await rig.up()
    assert again is not failed and again.state == READY and again.booted_by_us and again.may_shut_down


async def test_a_scope_switched_off_on_a_shared_device_lets_go_of_it_and_the_others_keep_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_mode="shared")
    shared = await rig.up("tp-1")
    assert await rig.manager.ensure(scope("tp-2")) is shared
    first_closed, first = closer_log()
    second_closed, second = closer_log()
    rig.manager.attach(shared, first, "tp-1")
    rig.manager.attach(shared, second, "tp-2")
    rig.config.set_for("tp-1", enabled=False)
    await rig.manager.reconcile()
    off = "The iOS Simulator is off for this project (`sim-mirror config`)."
    assert shared.state == READY and first_closed == [(CLOSE_FORBIDDEN, off)] and second_closed == []
    assert shared.owner.id == "tp-2" and shared.scopes == {"tp-2"} and set(shared.members) == {"tp-2"}
    assert rig.manager.instance(scope("tp-1")) is None and shared.viewers == 1
    rig.config.set_for("tp-2", enabled=False)
    assert await rig.manager.reap() == [shared.udid]
    assert shared.state == STOPPED and second_closed == [(CLOSE_FORBIDDEN, off)]


async def test_when_the_first_scope_on_a_shared_device_lets_go_the_next_one_owns_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(device_mode="shared")
    shared = await rig.up("tp-1")
    await rig.manager.ensure(scope("tp-2"))
    assert set(shared.members) == {"tp-1", "tp-2"} and shared.owner.id == "tp-1"
    await rig.manager.stop(scope("tp-1"))
    assert shared.owner.id == "tp-2" and set(shared.members) == {"tp-2"} and shared.state == READY
