# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent on a device: found running, started from its build and waited for, built for the device by itself
once a person set it up for the team, ended, and cleaned up after."""

from __future__ import annotations

import json
import signal
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.connectors.helper_process import helper_id
from sim_mirror.connectors.iphone.wda import WdaService
from sim_mirror.connectors.iphone.wda_setup import DEVICES_FILE, WdaSetup, derived_for, wda_root
from sim_mirror.host_copy import HostCopy
from sim_mirror.testing.fakes import PHONE_UDID, FakeProcess, FakeXcrun, ManualClock
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.wda import ARCHIVE, TEST_RELEASE, FakeWda, builds_wda, left_built

TEAM = "9Q48L5C2K5"
XCODE = "/Applications/Xcode.app/Contents/Developer"
CONFIG = SimConfig.defaults().with_values(real_devices_team_id=TEAM, wda_startup_timeout=30)


@dataclass
class World:
    """WebDriverAgent's device, its build, and what the service did to them."""

    wda: FakeWda
    root: Path
    logs: Path
    clock: ManualClock = field(default_factory=lambda: ManualClock(0.0))
    started: list[tuple[Any, ...]] = field(default_factory=list)
    signals: list[tuple[int, int]] = field(default_factory=list)
    processes: list[FakeProcess] = field(default_factory=list)
    #: What the next start does: comes up, exits, or never answers.
    then: str = "up"
    alive: set[int] = field(default_factory=set)
    commands: dict[int, str] = field(default_factory=dict)
    xcrun: FakeXcrun = field(default_factory=FakeXcrun)
    ready: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.setup = WdaSetup(root=lambda: self.root, fetch=lambda url: ARCHIVE, xcrun=self.xcrun, release=TEST_RELEASE)
        self.setup.on_ready = self.ready.append

    def built(self, devices: tuple[str, ...] = (PHONE_UDID,)) -> Path:
        """A build for the team and Xcode, made for these devices."""
        derived = derived_for(self.root, TEAM, XCODE)
        path = left_built(derived)
        (derived / DEVICES_FILE).write_text(json.dumps(list(devices)))
        return path

    async def start(self, test_run: Path, udid: str, team: str, developer_dir: str, log: Path) -> FakeProcess:
        if self.then == "refuse":
            raise OSError("xcrun is missing")
        self.started.append((test_run, udid, team, developer_dir, log))
        process = FakeProcess(5000 + len(self.processes))
        self.processes.append(process)
        if self.then == "up":
            await self.wda.serve()
        elif self.then == "exit":
            log.write_text("Testing started\n\nTesting failed: the developer is not trusted\n")
            process.finish(65)
        elif self.then == "unprofiled":
            log.write_text("Failed to install embedded profile: This provisioning profile cannot be installed\n")
            process.finish(65)
        elif self.then == "quiet":
            process.finish(1)
        return process

    def signal_group(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        for process in self.processes:
            if process.pid == pid:
                process.finish(-sig)
        self.alive.discard(pid)

    async def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    async def command_of(self, pid: int) -> str | None:
        return self.commands.get(pid)

    def service(self, run: Path, owner: int = 700) -> WdaService:
        return WdaService(
            run_dir=run,
            log_dir=self.logs,
            owner_tag="SimMirrorTest",
            copy=HostCopy(),
            setup=self.setup,
            opener_for=lambda udid: self.wda.opener(),
            start=self.start,
            signal_group=self.signal_group,
            pid_alive=lambda pid: pid in self.alive,
            command_of=self.command_of,
            clock=self.clock,
            sleep=self.sleep,
            owner=owner,
        )


@pytest.fixture
def run() -> Iterator[Path]:
    with short_run_dir() as folder:
        yield folder


@pytest.fixture
async def world(tmp_path: Path, run: Path) -> AsyncIterator[World]:
    fake = FakeWda(run)
    logs = tmp_path / "logs"
    logs.mkdir()
    made = World(fake, tmp_path / "wda", logs)
    try:
        yield made
    finally:
        await fake.stop()


def test_webdriveragent_is_kept_under_the_state_folder_one_build_per_team_and_xcode(tmp_path: Path) -> None:
    assert wda_root({"SIM_MIRROR_STATE_DIR": str(tmp_path)}) == tmp_path / "wda"
    assert derived_for(tmp_path, TEAM, XCODE).parent == tmp_path / "derived"
    assert derived_for(tmp_path, TEAM, XCODE) != derived_for(tmp_path, TEAM, "/Applications/Xcode27.app")


async def test_webdriveragent_already_running_is_used_as_it_is(world: World, run: Path) -> None:
    await world.wda.serve()
    service = world.service(run)
    client = await service.client(PHONE_UDID, CONFIG, XCODE)
    assert await client.status() is not None and world.started == [] and service.alive(PHONE_UDID)
    await service.stop(PHONE_UDID)  # none of its own to end
    assert world.signals == []


async def test_it_is_started_from_its_build_and_ended_with_its_pid_file(world: World, run: Path) -> None:
    test_run = world.built()
    service = world.service(run)
    await service.client(PHONE_UDID, CONFIG, XCODE)
    ((started, udid, team, xcode, log),) = world.started
    assert (started, udid, team, xcode) == (test_run, PHONE_UDID, TEAM, XCODE)
    assert log == world.logs / f"wda-{helper_id(PHONE_UDID)}.log"
    pid_file = run / "wda" / f"{helper_id(PHONE_UDID)}.pid"
    assert pid_file.read_text() == "5000 700 SimMirrorTest" and service.alive(PHONE_UDID)
    await service.shutdown()
    assert world.signals == [(5000, signal.SIGTERM)] and not pid_file.exists() and service.alive(PHONE_UDID)


async def test_it_is_refused_without_a_team_a_build_or_a_start(world: World, run: Path) -> None:
    service = world.service(run)
    copy = HostCopy()
    with pytest.raises(ConnectorUnavailable, match=r"real_devices.team_id"):
        await service.client(PHONE_UDID, CONFIG.with_values(real_devices_team_id=""), XCODE)
    with pytest.raises(ConnectorUnavailable) as unbuilt:
        await service.client(PHONE_UDID, CONFIG, XCODE)
    assert str(unbuilt.value) == copy.wda_offer(PHONE_UDID) and unbuilt.value.status == 409
    assert world.setup.state(TEAM, XCODE) is None, "a person sets it up the first time"
    world.built()
    world.then = "refuse"
    with pytest.raises(ConnectorUnavailable, match="could not be started: xcrun is missing"):
        await service.client(PHONE_UDID, CONFIG, XCODE)


async def test_one_that_ends_as_it_starts_says_what_it_said_and_what_to_do_on_the_device(
    world: World, run: Path
) -> None:
    world.built()
    world.then = "exit"
    service = world.service(run)
    with pytest.raises(ConnectorUnavailable, match="Testing failed: the developer is not trusted") as ended:
        await service.client(PHONE_UDID, CONFIG, XCODE)
    assert "Enable UI Automation" in str(ended.value) and not (run / "wda" / f"{helper_id(PHONE_UDID)}.pid").exists()
    world.then = "unprofiled"
    with pytest.raises(ConnectorUnavailable, match=f"wda setup --device {PHONE_UDID}"):
        await service.client(PHONE_UDID, CONFIG, XCODE)
    (world.logs / f"wda-{helper_id(PHONE_UDID)}.log").unlink()
    world.then = "quiet"
    with pytest.raises(ConnectorUnavailable, match="it said nothing"):
        await service.client(PHONE_UDID, CONFIG, XCODE)


async def test_a_team_set_up_before_is_built_for_another_device_by_itself_and_the_device_attached_again(
    world: World, run: Path
) -> None:
    world.built(devices=("00008110-000000000000AAAA",))
    builds_wda(world.xcrun)
    service = world.service(run)
    copy = HostCopy()
    with pytest.raises(ConnectorUnavailable) as building:
        await service.client(PHONE_UDID, CONFIG, XCODE)
    assert str(building.value) == copy.wda_building(TEAM)
    setup = world.setup.state(TEAM, XCODE)
    assert setup is not None and setup.udid == PHONE_UDID
    with pytest.raises(ConnectorUnavailable, match="being built"):
        await service.client(PHONE_UDID, CONFIG, XCODE)
    await world.setup.wait(setup)
    assert setup.state == "ready" and world.ready == [PHONE_UDID]
    assert f"id={PHONE_UDID}" in world.xcrun.calls[-1].args and len(world.xcrun.calls) == 1
    await service.client(PHONE_UDID, CONFIG, XCODE)
    assert world.started and world.started[0][1] == PHONE_UDID
    await service.shutdown()


async def test_a_setup_that_failed_is_shown_not_tried_again_and_again(world: World, run: Path) -> None:
    world.built(devices=())
    world.xcrun.on("xcodebuild", "build-for-testing", rc=65, out="error: No Account for Team\n")
    service = world.service(run)
    with pytest.raises(ConnectorUnavailable, match="being built"):
        await service.client(PHONE_UDID, CONFIG, XCODE)
    setup = world.setup.state(TEAM, XCODE)
    assert setup is not None
    await world.setup.wait(setup)
    with pytest.raises(ConnectorUnavailable) as failed:
        await service.client(PHONE_UDID, CONFIG, XCODE)
    assert str(failed.value) == HostCopy().wda_setup_failed("error: No Account for Team")
    assert len(world.xcrun.calls) == 1 and world.ready == []


async def test_one_that_never_answers_is_given_up_on_and_ended(world: World, run: Path) -> None:
    world.built()
    world.then = "silent"
    service = world.service(run)
    with pytest.raises(ConnectorUnavailable, match="did not answer within 30 seconds") as slow:
        await service.client(PHONE_UDID, CONFIG, XCODE)
    assert slow.value.status == 504 and world.signals == [(5000, signal.SIGTERM)] and world.clock.now >= 30


async def test_a_run_a_previous_host_left_is_ended_and_others_are_left_alone(world: World, run: Path) -> None:
    folder = run / "wda"
    folder.mkdir()
    (folder / "left.pid").write_text("81 999 SimMirrorTest")  # its host is gone, it still runs xcodebuild
    (folder / "gone.pid").write_text("82 999 SimMirrorTest")  # its host and it are both gone
    (folder / "other.pid").write_text("83 999 SomeoneElse")  # another host's
    (folder / "live.pid").write_text("84 998 SimMirrorTest")  # its host still runs
    (folder / "broken.pid").write_text("not a pid")
    world.alive |= {81, 83, 84, 998}
    world.commands.update({81: "/usr/bin/xcodebuild test-without-building", 84: "xcodebuild"})
    assert await world.service(run).reap_orphans() == 1
    assert world.signals == [(81, signal.SIGTERM)]
    assert sorted(path.name for path in folder.iterdir()) == ["broken.pid", "live.pid", "other.pid"]
