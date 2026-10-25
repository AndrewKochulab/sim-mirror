# SPDX-License-Identifier: Apache-2.0
"""The android connector: whether it can be used here, the session it composes for an emulator and a phone, and the
contract every connector keeps."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.android.connector import (
    ADB,
    EMULATOR_ONLY,
    AndroidConnector,
    AndroidRotator,
    TransportWatch,
    create,
)
from sim_mirror.connectors.android.geometry import Rotation
from sim_mirror.connectors.base import Capability, ConnectorUnavailable
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.core.android_control import AndroidControl
from sim_mirror.core.android_devices import AndroidDirectory, AndroidServices
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.android_sdk import AndroidSdk
from sim_mirror.platform.emulator import Emulators
from sim_mirror.platform.identifiers import android_id
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.contract import check_connector
from sim_mirror.testing.fakes import MemoryStateStore

PIXEL = android_id("avd", "Pixel_9")
PHONE = android_id("adb", "R58M12ABC")


def sdk_at(root: Path) -> AndroidSdk:
    for folder, binary in (("platform-tools", "adb"), ("emulator", "emulator")):
        (root / folder).mkdir(parents=True)
        (root / folder / binary).write_text("")
        (root / folder / "source.properties").write_text("Pkg.Revision=37.0.0\n")
    return AndroidSdk(root)


class Rig:
    def __init__(self, root: Path, server: FakeAdbServer) -> None:
        self.server = server
        self.sdk = sdk_at(root / "sdk")
        adb = server.adb()
        self.services = AndroidServices(
            adb,
            AndroidDirectory(adb, avds_of=lambda sdk: []),
            Emulators(root / "run", root / "logs", "host-a"),
            sdk_path=str(self.sdk.root),
        )
        self.connector = AndroidConnector(self.services)
        self.config = SimConfig.defaults().with_values(android_sdk_path=str(self.sdk.root))


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    async with FakeAdbServer() as server:
        yield Rig(tmp_path, server)


async def test_it_can_be_used_with_an_sdk_that_has_adb_and_says_why_not_otherwise(rig: Rig, tmp_path: Path) -> None:
    report = await rig.connector.probe(rig.config)
    assert report.available and report.capabilities == ADB | EMULATOR_ONLY and report.kinds == {"android"}
    assert report.versions == {"adb": "37.0.0", "emulator": "37.0.0"}
    off = await rig.connector.probe(rig.config.with_values(android=False))
    assert not off.available and off.reasons == ("Android devices are off here (android.enabled).",)
    missing = await rig.connector.probe(rig.config.with_values(android_sdk_path=str(tmp_path / "none")))
    assert not missing.available and "No Android SDK with adb" in missing.reasons[0]
    (rig.sdk.root / "emulator" / "emulator").unlink()
    assert (await rig.connector.probe(rig.config)).versions == {"adb": "37.0.0"}


async def test_an_emulator_keeps_the_contract_every_connector_does(rig: Rig) -> None:
    rig.server.emulator()
    rig.services.directory = AndroidDirectory(rig.services.adb, avds_of=lambda sdk: [])
    assert await check_connector(rig.connector, rig.config, android_id("avd", "Pixel_9")) == []


async def test_an_emulator_is_driven_whole_and_a_phone_without_a_location(rig: Rig) -> None:
    rig.server.emulator().phone()
    emulator = await rig.connector.attach(PIXEL, rig.config)
    assert emulator.capabilities == ADB | EMULATOR_ONLY and emulator.fps_limit == 4
    assert emulator.pressable == ("back", "home", "app_switch", "lock", "volume_up", "volume_down")
    assert emulator.note is not None and "set scrcpy up" in emulator.note
    phone = await rig.connector.attach(PHONE, rig.config)
    assert phone.capabilities == ADB and Capability.LOCATION not in phone.capabilities
    await emulator.close()
    await phone.close()


async def test_a_locked_screen_is_said_and_never_unlocked(rig: Rig) -> None:
    rig.server.emulator().on("dumpsys window policy", out="    KeyguardStateMonitor\n      mIsShowing=true\n")
    session = await rig.connector.attach(PIXEL, rig.config)
    assert session.note is not None and "Its screen is locked" in session.note
    await session.close()


async def test_a_device_that_is_not_running_cannot_be_attached(rig: Rig) -> None:
    with pytest.raises(ConnectorUnavailable, match="cannot be reached") as raised:
        await rig.connector.attach(PIXEL, rig.config)
    assert raised.value.status == 409


async def test_a_session_ends_once_adb_no_longer_lists_its_device_ready(rig: Rig) -> None:
    rig.server.emulator()
    session = await rig.connector.attach(PIXEL, rig.config)
    assert session.alive
    rig.server.device("emulator-5554", "offline", transport_id="5554")
    for _ in range(50):
        if not session.alive:
            break
        await asyncio.sleep(0.01)
    assert not session.alive
    await session.close()


async def test_a_watch_whose_adb_server_goes_away_says_the_device_is_gone(tmp_path: Path) -> None:
    services = AndroidServices.build(MemoryStateStore(tmp_path))
    services.adb.address = str(tmp_path / "gone.sock")
    watch = TransportWatch(services.adb, "emulator-5554")
    for _ in range(50):
        if not watch.alive:
            break
        await asyncio.sleep(0.01)
    assert not watch.alive
    await watch.close()


async def test_a_watch_whose_device_list_ends_says_the_device_is_gone() -> None:
    from sim_mirror.platform.adb import AdbDevice

    class Ending:
        async def track(self) -> AsyncIterator[list[AdbDevice]]:
            yield [AdbDevice("emulator-5554", "device")]

    watch = TransportWatch(Ending(), "emulator-5554")  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    assert not watch.alive
    await watch.close()


async def test_turning_the_device_turns_its_display_and_asks_again_which_way_it_is(rig: Rig) -> None:
    rig.server.emulator()
    asked: list[int] = []

    async def ask() -> str:
        asked.append(1)
        return "mCurrentOrientation=1"

    rotation = Rotation(ask)
    await rotation.quarters()
    rotator = AndroidRotator(AndroidControl(rig.services.adb, rig.services.directory.serial), PIXEL, rotation)
    rig.services.directory = AndroidDirectory(rig.services.adb, avds_of=lambda sdk: [])
    await rotator.rotate("landscape_left")
    assert rig.server.commands()[-1] == "settings put system user_rotation 3"
    await rotation.quarters()
    assert len(asked) == 2
    assert await rig.connector.reap_orphans() == 0


def test_the_factory_shares_the_hosts_android_services_or_makes_its_own(tmp_path: Path) -> None:
    state = MemoryStateStore(tmp_path)
    services = AndroidServices.build(state)
    context = ConnectorContext(state=state, copy=HostCopy(), simctl_for=lambda developer_dir: None, android=services)  # type: ignore[arg-type,return-value]
    assert create(context)._services is services
    alone = ConnectorContext(state=state, copy=HostCopy(), simctl_for=lambda developer_dir: None)  # type: ignore[arg-type,return-value]
    assert isinstance(create(alone)._services, AndroidServices)
