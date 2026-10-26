# SPDX-License-Identifier: Apache-2.0
"""A person's Set up touch: how touching a scope's real device stands, building WebDriverAgent for it, starting it
again once built, and the device attached again when it is ready."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.iphone.wda_setup import WdaSetup
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.signing import SigningTeams
from sim_mirror.core.touch import ScrcpyTouchSetup, TouchRefused, TouchSetups
from sim_mirror.platform.keychain import Team
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import FULL_CONTROL, PHONE_UDID, FakeConnector, FakePhoneBackend, FakeXcrun
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope
from sim_mirror.testing.scrcpy import fetched_jars
from sim_mirror.testing.wda import ARCHIVE, TEST_RELEASE, builds_wda

SCOPE = scope("tp-1")
TEAM = "TESTTEAM01"


class Touch:
    """A scope whose device is a real one, and what sets touching it up."""

    def __init__(self, root: Path, *, capabilities: frozenset[Any] = VIEW_ONLY, teams: tuple[Team, ...] = ()) -> None:
        self.rig = DeviceRig(
            root,
            phones=FakePhoneBackend(),
            phone=FakeConnector("phone", capabilities=capabilities, kinds=frozenset({"physical"})),
        )
        self.xcrun = FakeXcrun()
        self.setup = WdaSetup(
            root=lambda: root / "wda", fetch=lambda url: ARCHIVE, xcrun=self.xcrun, release=TEST_RELEASE
        )
        self.setup.on_ready = self.rig.manager.reattach

        async def mac() -> list[Team]:
            return list(teams)

        self.signing = SigningTeams(lambda _: None, mac)
        self.touch = TouchSetups(self.rig.manager, self.rig.config, self.signing, self.setup, self.rig.copy)

    async def pick_phone(self) -> None:
        await self.rig.manager.choose(SCOPE, PHONE_UDID)
        await self.rig.up()

    async def recovered(self) -> None:
        instance = self.rig.manager.instance(SCOPE)
        assert instance is not None and instance.recovery is not None
        await instance.recovery


async def test_a_simulator_or_no_device_has_nothing_to_set_up_and_a_person_is_told_so(tmp_path: Path) -> None:
    here = Touch(tmp_path)
    assert (await here.touch.status(SCOPE))["state"] == "not_needed"
    await here.rig.up()
    assert await here.touch.status(SCOPE) == {"state": "not_needed", "message": "", "team": None, "team_from": None}
    with pytest.raises(TouchRefused, match="for a real device"):
        await here.touch.set_up(SCOPE)


async def test_a_phone_is_set_up_once_built_for_and_attached_again_when_it_is_ready(tmp_path: Path) -> None:
    here = Touch(tmp_path)
    here.rig.config.set(real_devices_team_id=TEAM)
    builds_wda(here.xcrun)
    await here.pick_phone()
    offered = await here.touch.status(SCOPE)
    assert offered == {
        "state": "offer",
        "message": here.rig.copy.wda_offer(PHONE_UDID),
        "team": TEAM,
        "team_from": "setting",
    }
    building = await here.touch.set_up(SCOPE)
    assert building["state"] == "building" and building["message"] == here.rig.copy.wda_building(TEAM)
    setup = here.setup.state(TEAM, here.rig.manager.instance(SCOPE).developer_dir)  # type: ignore[union-attr]
    assert setup is not None
    await here.setup.wait(setup)
    await here.recovered()  # the device was attached again once it was ready
    after = await here.touch.status(SCOPE)
    assert after["state"] == "failed", "built, yet the device still cannot be touched: its note says why"
    assert (await here.touch.set_up(SCOPE))["state"] == "starting", "built: started again, not built again"
    await here.recovered()
    assert sum(call.args[1] == "build-for-testing" for call in here.xcrun.calls) == 1


async def test_a_setup_that_failed_says_why(tmp_path: Path) -> None:
    here = Touch(tmp_path, teams=(Team(TEAM, "Test Team", datetime(2126, 1, 1, tzinfo=timezone.utc)),))
    here.xcrun.on("xcodebuild", "build-for-testing", rc=65, out="error: No Account for Team\n")
    await here.pick_phone()
    await here.touch.set_up(SCOPE)
    setup = here.setup.state(TEAM, here.rig.manager.instance(SCOPE).developer_dir)  # type: ignore[union-attr]
    assert setup is not None
    await here.setup.wait(setup)
    failed = await here.touch.status(SCOPE)
    assert failed["state"] == "failed" and failed["team_from"] == "mac"
    assert failed["message"] == here.rig.copy.wda_setup_failed("error: No Account for Team")


async def test_what_stands_in_the_way_is_said_and_refused(tmp_path: Path) -> None:
    here = Touch(tmp_path)
    await here.pick_phone()
    needs = await here.touch.status(SCOPE)
    assert needs["state"] == "needs_team" and needs["message"] == here.rig.copy.wda_needs_team()
    with pytest.raises(TouchRefused, match="none is known here"):
        await here.touch.set_up(SCOPE)
    here.rig.config.set(wda_enabled=False)
    assert (await here.touch.status(SCOPE))["state"] == "off"
    with pytest.raises(TouchRefused, match=r"real_devices\.wda\.enabled"):
        await here.touch.set_up(SCOPE)


async def test_a_phone_that_can_be_touched_is_ready_while_what_drives_it_answers(tmp_path: Path) -> None:
    here = Touch(tmp_path, capabilities=FULL_CONTROL)
    await here.pick_phone()
    assert (await here.touch.status(SCOPE))["state"] == "ready", "a session that asks nothing is taken at its word"
    instance = here.rig.manager.instance(SCOPE)
    assert instance is not None and instance.session is not None
    silent: list[str | None] = [None]

    async def answers() -> str | None:
        return silent[0]

    instance.session.answers = answers
    assert (await here.touch.status(SCOPE))["state"] == "ready"
    silent[0] = here.rig.copy.wda_silent()
    said = await here.touch.status(SCOPE)
    assert said["state"] == "failed" and said["message"] == here.rig.copy.wda_silent()


async def test_a_setup_under_way_ends_with_the_host(tmp_path: Path) -> None:
    here = Touch(tmp_path)
    here.rig.config.set(real_devices_team_id=TEAM)
    started = asyncio.Event()

    async def forever(*args: str, **options: Any) -> XcrunResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("never answers")

    here.setup = WdaSetup(root=lambda: tmp_path / "wda", fetch=lambda url: ARCHIVE, xcrun=forever, release=TEST_RELEASE)
    here.touch = TouchSetups(here.rig.manager, here.rig.config, here.signing, here.setup)
    await here.pick_phone()
    await here.touch.set_up(SCOPE)
    await started.wait()
    await here.touch.shutdown()
    setup = here.setup.state(TEAM, here.rig.manager.instance(SCOPE).developer_dir)  # type: ignore[union-attr]
    assert setup is not None and setup.task is not None and setup.task.cancelled()


class Recorded:
    """A kind's own setup, saying what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def status(self, scope: Any, instance: Any) -> Any:
        self.asked.append(f"status {instance.udid}")
        return {"state": "offer", "message": "set it up", "team": None, "team_from": None}

    async def set_up(self, scope: Any, instance: Any) -> Any:
        self.asked.append(f"set_up {instance.udid}")
        return {"state": "building", "message": "", "team": None, "team_from": None}

    async def shutdown(self) -> None:
        self.asked.append("shutdown")


async def test_another_kind_of_device_is_set_up_by_what_sets_up_its_own_kind(tmp_path: Path) -> None:
    here = Touch(tmp_path)
    kind = Recorded()
    touch = TouchSetups(here.rig.manager, here.rig.config, here.signing, here.setup, others={"simulator": kind})
    instance = await here.rig.up()
    assert (await touch.status(SCOPE))["state"] == "offer"
    assert (await touch.set_up(SCOPE))["state"] == "building"
    await touch.shutdown()
    assert kind.asked == [f"status {instance.udid}", f"set_up {instance.udid}", "shutdown"]


class Reattached:
    """The part of a manager scrcpy's setup asks: attach a device again."""

    def __init__(self) -> None:
        self.udids: list[str] = []

    def reattach(self, udid: str) -> None:
        self.udids.append(udid)


def android_instance(capabilities: frozenset[Any] = frozenset()) -> DeviceInstance:
    return DeviceInstance(
        udid="avd-Pixel-9-3fa2c1", name="Pixel 9", runtime="Android 16 (API 36)", owner=SCOPE, developer_dir="",
        scopes={SCOPE.id}, created=False, connector="android", capabilities=capabilities, kind="android",
    )  # fmt: skip


async def test_an_android_device_is_offered_scrcpy_fetched_once_then_attached_again_with_it(tmp_path: Path) -> None:
    manager, rig = Reattached(), DeviceRig(tmp_path)
    jars = fetched_jars(tmp_path / "jars", fetched=False)
    setup = ScrcpyTouchSetup(manager, rig.config, jars)  # type: ignore[arg-type]
    device = android_instance()
    offered = await setup.status(SCOPE, device)
    assert offered["state"] == "offer" and "fetches scrcpy's server 9.9.9 from GitHub" in offered["message"]
    assert offered["team"] is None
    building = await setup.set_up(SCOPE, device)
    assert building["state"] == "building" and "9.9.9" in building["message"]
    assert jars.fetching is not None and jars.fetching.task is not None
    await jars.fetching.task
    # Set up, and the device not yet attached again: SimMirror is starting it, or it failed and says why.
    device.recovery = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
    assert (await setup.status(SCOPE, device))["state"] == "starting"
    device.recovery = None
    device.fallback_reason = "scrcpy's server could not start (Aborted)."
    assert await setup.status(SCOPE, device) == {
        "state": "failed", "message": "scrcpy's server could not start (Aborted).", "team": None, "team_from": None,
    }  # fmt: skip
    assert (await setup.set_up(SCOPE, device))["state"] == "failed" and manager.udids == [device.udid]
    live = android_instance(frozenset({Capability.STREAM_H264}))
    assert (await setup.status(SCOPE, live))["state"] == "ready"
    await setup.shutdown()


async def test_scrcpy_off_or_a_server_simmirror_does_not_know_is_refused(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    jars = fetched_jars(tmp_path / "jars", fetched=False)
    setup = ScrcpyTouchSetup(Reattached(), rig.config, jars)  # type: ignore[arg-type]
    rig.config.set(scrcpy=False)
    assert (await setup.status(SCOPE, android_instance()))["state"] == "off"
    with pytest.raises(TouchRefused, match="scrcpy is off here"):
        await setup.set_up(SCOPE, android_instance())
    rig.config.set(scrcpy=True, scrcpy_path=str(tmp_path / "nothing"))
    with pytest.raises(TouchRefused, match="is not a scrcpy server SimMirror knows"):
        await setup.set_up(SCOPE, android_instance())
    failing = fetched_jars(tmp_path / "failing", fetched=False)
    failing._fetch = lambda url: b"tampered"
    failed = ScrcpyTouchSetup(Reattached(), rig.config, failing)  # type: ignore[arg-type]
    rig.config.set(scrcpy_path="")
    await failed.set_up(SCOPE, android_instance())
    assert failing.fetching is not None and failing.fetching.task is not None
    await failing.fetching.task
    assert (await failed.status(SCOPE, android_instance()))["state"] == "failed"
