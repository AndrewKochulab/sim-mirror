# SPDX-License-Identifier: Apache-2.0
"""What the supervisor does on its own and what it leaves for a call: a real device's ended session is left ended -- its
WebDriverAgent killed means a person took the phone back -- until the next call starts it over, idle agents stop
holding it, scopes that held a device started over keep it, and a device being started over holds up only itself."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.core.instance import FAILED, READY, STOPPED, DeviceInstance
from sim_mirror.core.manager import SESSION_ENDED_NOTE
from sim_mirror.daemon.lease import LEASE_S, Leases
from sim_mirror.mcp.launcher import LeaseKeeper
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakePhoneBackend, ManualClock
from sim_mirror.testing.rig import DeviceRig, scope, settle

TP1 = scope("tp-1")
AGENT_IDLE_S = 30 * 60


class IdleRelay:
    """A ``sim-mirror mcp`` whose client made a call and then sat there: its own lease keeper, renewing into the
    daemon's leases as `DaemonClient.lease` would."""

    def __init__(self, leases: Leases, scope_id: str, clock: ManualClock) -> None:
        self.leases = leases
        self.scope_id = scope_id
        self.keeper = LeaseKeeper(self, f"token-{scope_id}", clock=clock)  # type: ignore[arg-type]

    def lease(self, token: str) -> dict[str, Any]:
        self.leases.renew(self.scope_id, token)
        return {"scope": self.scope_id, "expires_in_s": LEASE_S, "agent_idle_s": AGENT_IDLE_S}

    def tick(self) -> None:
        """What its lease thread does when it wakes."""
        if self.keeper.wait_s() == 0:
            self.keeper.renew()


def phone_rig(tmp_path: Path) -> tuple[DeviceRig, FakeConnector]:
    phone = FakeConnector("phone", kinds=frozenset({"physical"}))
    return DeviceRig(tmp_path, phones=FakePhoneBackend(), phone=phone), phone


async def on_the_phone(rig: DeviceRig, *scope_ids: str) -> DeviceInstance:
    """The phone, picked and brought up for each scope: the first starts it, the others join."""
    instance: DeviceInstance | None = None
    for scope_id in scope_ids:
        await rig.manager.choose(scope(scope_id), PHONE_UDID)
        instance = await rig.up(scope_id)
    assert instance is not None
    return instance


async def test_webdriveragent_killed_under_idle_agents_stays_down_until_the_next_call_brings_it_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The field report's acceptance for B1: five idle relays, WebDriverAgent killed on the phone; it stays down for ten
    minutes of reaper rounds, and the next call brings it back. Then, with no more calls, the relays stop renewing and
    the phone is let go as idle as usual."""
    caplog.set_level(logging.INFO, logger="sim_mirror.core.manager")
    rig, phone = phone_rig(tmp_path)
    leases = Leases(clock=rig.clock)
    rig.manager.usage = leases
    ids = [f"tp-{n}" for n in range(1, 6)]
    instance = await on_the_phone(rig, *ids)
    relays = [IdleRelay(leases, scope_id, rig.clock) for scope_id in ids]
    for relay in relays:
        relay.keeper.called()
        relay.tick()
    assert instance.scopes == set(ids) and leases.scopes() == set(ids)

    async def minutes(count: int) -> list[str]:
        """Time passing in 30-second steps: the relays wake, and the reaper runs once a minute."""
        ended: list[str] = []
        for step in range(count * 2):
            rig.clock.advance(30)
            for relay in relays:
                relay.tick()
            if step % 2:
                ended += await rig.manager.reap()
        return ended

    phone.dead.add(PHONE_UDID)  # `devicectl device process terminate` on WebDriverAgentRunner
    assert await minutes(10) == []
    assert phone.attached == [PHONE_UDID], "never started again by itself"
    assert instance.state == READY and instance.recovery is None and rig.manager.instance(TP1) is instance
    assert instance.session is not None and not instance.session.alive
    assert instance.fallback_reason == SESSION_ENDED_NOTE
    assert caplog.text.count("it starts again on the next call") == 1, "said once, not every round"
    relays[2].keeper.called()
    again = await rig.manager.ensure(scope("tp-3"))
    assert again is instance and phone.attached == [PHONE_UDID, PHONE_UDID], "the next call brings it back"
    assert instance.session is not None and instance.session.alive and instance.fallback_reason is None
    assert "session had ended; starting it again for a call" in caplog.text
    called_at = rig.clock.now
    ended = await minutes(44)
    assert ended == [] and leases.scopes() == set(), "half an hour after their last calls, nobody renews"
    ended = await minutes(4)
    assert ended == [PHONE_UDID] and instance.state == STOPPED, "then it is idle, and let go as any device is"
    assert 45 * 60 <= rig.clock.now - called_at <= 48 * 60
    assert phone.attached == [PHONE_UDID, PHONE_UDID]


async def test_a_simulator_s_stopped_connector_is_still_attached_again_by_the_reaper_saying_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="sim_mirror.core.manager")
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    rig.idb.dead.add(instance.udid)
    assert await rig.manager.reap() == []
    assert instance.recovery is not None
    await instance.recovery
    assert rig.idb.attached == [instance.udid, instance.udid]
    assert f"attaching the simulator {instance.udid} again: its connector stopped" in caplog.text


async def test_a_call_finding_its_device_s_session_ended_starts_it_over_at_once_rather_than_failing(
    tmp_path: Path,
) -> None:
    """The reaper only looks once a minute: a call in between used to be answered that the device cannot be reached."""
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    rig.idb.dead.add(instance.udid)
    assert await rig.manager.ensure(TP1) is instance
    assert rig.idb.attached == [instance.udid, instance.udid] and instance.session is not None
    assert instance.session.alive and instance.state == READY
    other = await rig.manager.ensure(scope("tp-2"))
    assert other is not instance, "another scope gets a device of its own"
    rig.idb.dead.add(instance.udid)
    await rig.manager.choose(scope("tp-3"), instance.udid)
    assert await rig.manager.ensure(scope("tp-3")) is instance, "one joining a device whose session ended, too"
    assert rig.idb.attached.count(instance.udid) == 3 and instance.session.alive


async def test_a_real_device_that_cannot_be_reached_again_for_a_call_fails_saying_why(tmp_path: Path) -> None:
    rig, phone = phone_rig(tmp_path)
    instance = await on_the_phone(rig, "tp-1")
    phone.dead.add(PHONE_UDID)
    await rig.manager.reap()
    phone.fail = ConnectorUnavailable("Test iPhone is not connected: plug it in and unlock it", 409)
    assert await rig.manager.ensure(TP1) is instance
    assert instance.state == FAILED and instance.reason is not None and "not connected" in instance.reason


class HeldFor:
    """A `UsageProbe` for agents holding some scopes only."""

    def __init__(self, *scope_ids: str) -> None:
        self.scope_ids = set(scope_ids)

    def in_use(self, device: Any) -> bool:
        return bool(self.scope_ids & set(device.scopes))


async def test_scopes_an_agent_holds_keep_a_device_started_over_by_another_and_the_rest_let_go(tmp_path: Path) -> None:
    rig, phone = phone_rig(tmp_path)
    rig.manager.usage = HeldFor("tp-2", "tp-4")
    failed = await on_the_phone(rig, "tp-1", "tp-2", "tp-3")
    phone.fail = ConnectorUnavailable("Test iPhone is not connected: plug it in and unlock it", 409)
    with pytest.raises(Exception, match="not connected"):
        await rig.manager.reconnect(TP1)
    assert failed.state == FAILED
    phone.fail = None
    fresh = await rig.up("tp-1")
    assert fresh is not failed and fresh.scopes == {"tp-1", "tp-2"}, "tp-2 is still held; tp-3 is not"
    assert rig.manager.instance(scope("tp-2")) is fresh and rig.manager.instance(scope("tp-3")) is None
    assert fresh.owner == TP1 and set(fresh.members) == {"tp-1", "tp-2"}
    assert await rig.manager.stop(scope("tp-2")) is True, "so stopping it says it stopped"
    # Started over by a scope that was not on it: those held keep it all the same.
    await on_the_phone(rig, "tp-4")
    phone.fail = ConnectorUnavailable("Test iPhone is not connected: plug it in and unlock it", 409)
    with pytest.raises(Exception, match="not connected"):
        await rig.manager.reconnect(TP1)
    phone.fail = None
    await rig.manager.choose(scope("tp-5"), PHONE_UDID)
    newer = await rig.up("tp-5")
    assert newer.scopes == {"tp-5", "tp-4"}


async def test_a_device_is_let_go_for_every_scope_using_it_and_they_are_named(tmp_path: Path) -> None:
    rig, _ = phone_rig(tmp_path)
    assert await rig.manager.let_go(PHONE_UDID) is None
    instance = await on_the_phone(rig, "tp-1", "tp-2")
    held = await rig.manager.let_go(PHONE_UDID)
    assert [member.id for member in held or ()] == ["tp-1", "tp-2"]
    assert (
        instance.state == STOPPED and rig.manager.instance(TP1) is None and rig.manager.instance(scope("tp-2")) is None
    )
    assert await rig.manager.stop(TP1) is False


def slow_to_start(phone: FakeConnector) -> asyncio.Event:
    """WebDriverAgent taking its time to start on the phone -- up to `real_devices.wda.startup_timeout`, three minutes
    by default -- until the event is set."""
    phone.release = asyncio.Event()
    return phone.release


Call = asyncio.Task[DeviceInstance]


async def reviving(rig: DeviceRig, phone: FakeConnector, *scope_ids: str) -> tuple[DeviceInstance, asyncio.Event, Call]:
    """The phone up for each scope, its WebDriverAgent killed, and a call for the first starting it over -- slowly."""
    instance = await on_the_phone(rig, *scope_ids)
    phone.dead.add(PHONE_UDID)
    started = slow_to_start(phone)
    call = asyncio.get_running_loop().create_task(rig.manager.ensure(scope(scope_ids[0])))
    await settle()
    assert instance.session_lock.locked() and phone.attached == [PHONE_UDID, PHONE_UDID]
    return instance, started, call


async def test_a_device_slow_to_start_again_holds_up_only_itself_not_other_devices_nor_the_reaper(
    tmp_path: Path,
) -> None:
    """Starting WebDriverAgent again for a call used to hold the manager's lock for as long as it took: every other
    device's call, the reaper and stop waited minutes behind one phone."""
    rig, phone = phone_rig(tmp_path)
    instance, started, call = await reviving(rig, phone, "tp-1")
    other = await asyncio.wait_for(rig.up("tp-2"), 1)
    assert other is not instance and other.state == READY, "another device, brought up meanwhile"
    assert await asyncio.wait_for(rig.manager.reap(), 1) == []
    assert await asyncio.wait_for(rig.manager.stop(scope("tp-2")), 1) is True
    assert not call.done()
    started.set()
    assert await call is instance
    assert instance.state == READY and instance.session is not None and instance.session.alive


async def test_two_calls_for_a_device_being_started_again_start_it_once(tmp_path: Path) -> None:
    rig, phone = phone_rig(tmp_path)
    instance, started, call = await reviving(rig, phone, "tp-1", "tp-2")
    second = asyncio.get_running_loop().create_task(rig.manager.ensure(scope("tp-2")))
    await settle()
    assert not second.done(), "it waits for the start-over under way"
    started.set()
    assert await call is instance and await second is instance
    assert phone.attached == [PHONE_UDID, PHONE_UDID], "started once, not twice"
    assert instance.session is not None and instance.session.alive


@pytest.mark.parametrize("by", ["stop", "let_go"])
async def test_a_device_let_go_while_it_is_started_again_ends_cleanly_and_a_new_one_waits_for_it(
    tmp_path: Path, by: str
) -> None:
    rig, phone = phone_rig(tmp_path)
    instance, started, call = await reviving(rig, phone, "tp-1")
    ended = rig.manager.stop(TP1) if by == "stop" else rig.manager.let_go(PHONE_UDID)
    assert await asyncio.wait_for(ended, 1), "not held up by the start-over"
    assert instance.state == STOPPED and rig.manager.instance(TP1) is None
    # Asked for again at once: the new instance is attached only once the old one's start-over is done with.
    await rig.manager.choose(TP1, PHONE_UDID)
    fresh = await rig.manager.ensure(TP1)
    assert fresh is not instance and fresh.session_lock is instance.session_lock
    await settle()
    assert phone.attached == [PHONE_UDID, PHONE_UDID]
    started.set()
    assert await call is instance
    assert fresh.task is not None
    await fresh.task
    assert instance.state == STOPPED and instance.session is None, "not failed, nor given the session started"
    assert phone.closed == [PHONE_UDID, PHONE_UDID], "the dead session, then the one started for nobody"
    assert phone.attached == [PHONE_UDID] * 3 and fresh.state == READY and fresh.session is not None
    assert fresh.session.alive


async def test_a_reconnect_waits_for_the_device_s_start_over_and_one_ended_meanwhile_answers_false(
    tmp_path: Path,
) -> None:
    rig, phone = phone_rig(tmp_path)
    instance, started, call = await reviving(rig, phone, "tp-1")
    waiting = asyncio.get_running_loop().create_task(rig.manager.reconnect(TP1))
    await settle()
    assert not waiting.done()
    started.set()
    assert await waiting is True and phone.attached == [PHONE_UDID] * 3, "then started over, as asked"
    await call
    started = slow_to_start(phone)
    reconnecting = asyncio.get_running_loop().create_task(rig.manager.reconnect(TP1))
    queued = asyncio.get_running_loop().create_task(rig.manager.reconnect(TP1))
    await settle()
    assert await asyncio.wait_for(rig.manager.let_go(PHONE_UDID), 1)
    started.set()
    assert await reconnecting is False and await queued is False
    assert instance.state == STOPPED and phone.attached == [PHONE_UDID] * 4


async def test_a_recovery_or_a_better_session_for_a_device_ended_or_started_over_meanwhile_is_let_go(
    tmp_path: Path,
) -> None:
    rig, phone = phone_rig(tmp_path)
    instance = await on_the_phone(rig, "tp-1")
    old = instance.session
    assert old is not None
    await rig.manager._recover(instance, None)
    assert phone.attached == [PHONE_UDID] and instance.session is old, "started over by something else meanwhile"
    started = slow_to_start(phone)
    recovering = asyncio.get_running_loop().create_task(rig.manager._recover(instance, old))
    await settle()
    await asyncio.wait_for(rig.manager.let_go(PHONE_UDID), 1)
    started.set()
    await recovering
    assert instance.state == STOPPED and phone.closed == [PHONE_UDID, PHONE_UDID]
    phone.release = None
    instance = await rig.up()
    old = instance.session
    assert old is not None
    started = slow_to_start(phone)
    upgrading = asyncio.get_running_loop().create_task(rig.manager._upgrade(instance, old))
    await settle()
    await asyncio.wait_for(rig.manager.let_go(PHONE_UDID), 1)
    started.set()
    await upgrading
    assert instance.state == STOPPED and instance.session is old and phone.closed[-2:] == [PHONE_UDID, PHONE_UDID]


async def test_webdriveragent_turned_off_while_a_session_is_attached_is_dropped_by_the_next_reaper_round(
    tmp_path: Path,
) -> None:
    """A better session attached once the cable is back starts WebDriverAgent too: the reaper does not wait for it."""
    rig, phone = phone_rig(tmp_path)
    instance = await on_the_phone(rig, "tp-1")
    old = instance.session
    assert old is not None
    started = slow_to_start(phone)
    upgrading = asyncio.get_running_loop().create_task(rig.manager._upgrade(instance, old))
    await settle()
    rig.config.set(wda_enabled=False)
    assert await asyncio.wait_for(rig.manager.reap(), 1) == [], "the reaper does not wait for it"
    assert instance.session is old and phone.attached == [PHONE_UDID, PHONE_UDID]
    started.set()
    await upgrading
    assert instance.session is not old and instance.session is not None
    await rig.manager.reap()
    assert instance.session is not None and instance.session.input is None, "then started over view only"
    assert phone.attached == [PHONE_UDID] * 3
    await rig.manager._view_only(instance)
    assert phone.attached == [PHONE_UDID] * 3, "one view only already is left as it is"
