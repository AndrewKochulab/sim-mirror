# SPDX-License-Identifier: Apache-2.0
"""Which device a scope uses: the newest iPhone by default, remembered by UDID, replaced when it is gone."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.core.devices import (
    DeviceDirectory,
    DeviceRef,
    JsonDeviceMemory,
    NoDevice,
    device_name,
    pick_device_type,
    pick_runtime,
)
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import DeviceType, Runtime, Simctl
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import BOOTED_UDID, FakeXcrun, MemoryStateStore

NEW = "11111111-2222-3333-4444-555555555555"
GONE = "99999999-9999-9999-9999-999999999999"
PHONE = DeviceType("t.phone", "iPhone 17 Pro", "iPhone")
OLD_PHONE = DeviceType("t.old", "iPhone 11", "iPhone")
PAD = DeviceType("t.pad", "iPad Pro", "iPad")
SCOPE = Scope(id="alpha-tp-1", group="alpha", label="alpha · tp-1")
OTHER = Scope(id="alpha-tp-2", group="alpha", label="alpha · tp-2")


def config(**overrides: object) -> SimConfig:
    return SimConfig.defaults().with_values(**overrides)


def runtime(name: str, version: str, *, platform: str = "iOS", available: bool = True,
            types: tuple[DeviceType, ...] = ()) -> Runtime:  # fmt: skip
    return Runtime(f"id.{name}", name, version, platform, available, types)


def test_the_newest_available_ios_runtime_is_the_default() -> None:
    runtimes = [
        runtime("iOS 18.6", "18.6"),
        runtime("iOS 26.5", "26.5"),
        runtime("iOS 27.0", "27.0", available=False),
        runtime("tvOS 26.5", "26.5", platform="tvOS"),
        runtime("iOS 26.10", "26.10"),
    ]
    assert pick_runtime(runtimes, "").name == "iOS 26.10"  # type: ignore[union-attr]
    assert pick_runtime(runtimes, "iOS 18.6").name == "iOS 18.6"  # type: ignore[union-attr]
    assert pick_runtime(runtimes, "26.5").name == "iOS 26.5"  # type: ignore[union-attr]
    assert pick_runtime(runtimes, "iOS 27.0") is None and pick_runtime([], "") is None


def test_the_newest_iphone_is_the_default_device_type() -> None:
    assert pick_device_type(runtime("iOS", "26.5", types=(PAD, PHONE, OLD_PHONE)), "") == PHONE
    assert pick_device_type(runtime("iOS", "26.5", types=(PHONE, PAD)), "iPad Pro") == PAD
    assert pick_device_type(runtime("iOS", "26.5", types=(PHONE,)), "iPhone 99") is None
    assert pick_device_type(runtime("iOS", "26.5", types=(PAD,)), "") == PAD
    assert pick_device_type(runtime("iOS", "26.5"), "") is None


def test_a_made_device_is_named_for_its_scope_or_the_group_sharing_it() -> None:
    assert device_name("SimMirror", SCOPE, shared=False) == "SimMirror · alpha · tp-1"
    assert device_name("Host", SCOPE, shared=True) == "Host · alpha"


@pytest.fixture
def made() -> FakeXcrun:
    return FakeXcrun().with_lists().on("simctl", "create", out=NEW + "\n")


def directory(tmp_path: Path) -> DeviceDirectory:
    return DeviceDirectory(
        JsonDeviceMemory(MemoryStateStore(tmp_path).devices_file()), HostCopy(simulator_settings="the settings")
    )


async def test_a_scope_with_no_device_gets_the_newest_iphone_and_it_is_remembered(
    tmp_path: Path, made: FakeXcrun
) -> None:
    found = directory(tmp_path)
    ref = await found.resolve(Simctl(made), SCOPE, config())
    assert ref == DeviceRef(NEW, "SimMirror · alpha · tp-1", "iOS 26.5", created=True)
    assert made.argv()[-1] == (
        "simctl",
        "create",
        "SimMirror · alpha · tp-1",
        "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
        "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
    )
    assert found.memory.assigned(SCOPE, False) == NEW and found.memory.created(SCOPE) == {NEW}
    stored = tmp_path / "state" / "devices.json"
    assert json.loads(stored.read_text()) == {"created": [NEW], "scopes": {SCOPE.id: NEW}}
    assert stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    assert [path.name for path in stored.parent.iterdir()] == ["devices.json"]


async def test_a_remembered_device_that_exists_is_used_as_it_is(tmp_path: Path, made: FakeXcrun) -> None:
    found = directory(tmp_path)
    found.memory.choose(SCOPE, False, BOOTED_UDID)
    ref = await found.resolve(Simctl(made), SCOPE, config())
    assert ref == DeviceRef(BOOTED_UDID, "iPhone 17 Pro", "iOS 26.5", created=False)
    assert [args[1] for args in made.argv()] == ["list"]


async def test_a_remembered_device_that_is_gone_is_replaced(tmp_path: Path, made: FakeXcrun) -> None:
    found = directory(tmp_path)
    found.memory.choose(SCOPE, False, GONE)
    ref = await found.resolve(Simctl(made), SCOPE, config())
    assert ref.udid == NEW and found.memory.assigned(SCOPE, False) == NEW


async def test_a_shared_group_has_one_device_for_every_scope(tmp_path: Path, made: FakeXcrun) -> None:
    found = directory(tmp_path)
    shared = config(device_mode="shared", runtime="iOS 18.6", device_type="iPhone 16e")
    first = await found.resolve(Simctl(made), SCOPE, shared)
    assert first.name == "SimMirror · alpha" and first.runtime == "iOS 18.6"
    assert made.argv()[-1][3] == "com.apple.CoreSimulator.SimDeviceType.iPhone-16e"
    listed = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-6": [
        {"udid": NEW, "name": "SimMirror · alpha", "state": "Shutdown", "isAvailable": True,
         "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-16e"}]}}  # fmt: skip
    made.on("simctl", "list", "devices", "-j", out=json.dumps(listed))
    second = await found.resolve(Simctl(made), OTHER, shared)
    assert second == DeviceRef(NEW, "SimMirror · alpha", "iOS 18.6", created=True)
    assert found.memory.assigned(Scope(id="alpha-tp-9", group="alpha", label="x"), True) == NEW


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"runtime": "iOS 30.0"}, r"No iOS 30\.0 simulator runtime is installed \(the settings\)"),
        ({"device_type": "Galaxy"}, "iOS 26.5 has no device type named 'Galaxy'"),
    ],
)
async def test_what_cannot_be_had_says_why(
    tmp_path: Path, made: FakeXcrun, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(NoDevice, match=message):
        await directory(tmp_path).resolve(Simctl(made), SCOPE, config(**overrides))
    assert NoDevice.status == 409


async def test_a_mac_with_no_ios_runtime_or_no_devices_in_it_says_so(tmp_path: Path) -> None:
    tv = {"identifier": "tv", "name": "tvOS 26.5", "version": "26.5", "platform": "tvOS", "isAvailable": True}
    tv_only = FakeXcrun().on("simctl", "list", "runtimes", "-j", out=json.dumps({"runtimes": [tv]}))
    with pytest.raises(NoDevice, match="No iOS simulator runtime is installed"):
        await directory(tmp_path).resolve(Simctl(tv_only), SCOPE, config())
    bare = FakeXcrun().on("simctl", "list", "runtimes", "-j", out=json.dumps({"runtimes": [
        {"identifier": "ios", "name": "iOS 26.5", "version": "26.5", "platform": "iOS", "isAvailable": True,
         "supportedDeviceTypes": []}]}))  # fmt: skip
    with pytest.raises(NoDevice, match=r"iOS 26\.5 runs no iPhone or iPad"):
        await directory(tmp_path).resolve(Simctl(bare), SCOPE, config())


def test_choosing_forgetting_and_a_file_that_cannot_be_read(tmp_path: Path) -> None:
    memory = JsonDeviceMemory(MemoryStateStore(tmp_path).devices_file())
    assert memory.assigned(SCOPE, False) is None and memory.forget(SCOPE, False) is None
    memory.choose(SCOPE, False, BOOTED_UDID)
    assert memory.forget(SCOPE, False) == BOOTED_UDID and memory.assigned(SCOPE, False) is None
    stored = tmp_path / "state" / "devices.json"
    stored.write_text("{broken")
    assert memory.assigned(SCOPE, False) is None and memory.created(SCOPE) == set()
    stored.write_text(json.dumps({"scopes": ["x"], "created": "y"}))
    assert memory.created(SCOPE) == set() and memory.assigned(SCOPE, False) is None
    stored.write_text("[1]")
    assert memory.assigned(SCOPE, False) is None
    assert DeviceDirectory(memory).memory is memory
