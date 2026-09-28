# SPDX-License-Identifier: Apache-2.0
"""Each kind of device has its backend: the manager lists both, brings a real device up without booting it, never
shuts one down, never counts one against max_booted, and never swaps a remembered one for a simulator."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.backends import SimulatorBackend
from sim_mirror.core.control import SimulatorControl
from sim_mirror.core.devices import DeviceDirectory, JsonDeviceMemory
from sim_mirror.core.instance import READY, STOPPED
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, PHONE_UDID, FakeConnector, FakePhoneBackend, FakeXcrun
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope

PHYSICAL = frozenset({"physical"})
TP1 = scope("tp-1")


def phone_rig(tmp_path: Path, **changes: object) -> DeviceRig:
    phone = FakeConnector("phone", kinds=PHYSICAL, capabilities=VIEW_ONLY)
    options: dict[str, object] = {"phones": FakePhoneBackend(), "phone": phone, **changes}
    return DeviceRig(tmp_path, **options)  # type: ignore[arg-type]


async def test_the_picker_lists_simulators_then_real_devices_and_leaves_out_a_kind_it_cannot_list(
    tmp_path: Path,
) -> None:
    rig = phone_rig(tmp_path)
    listed = await rig.manager.devices(TP1)
    assert {choice["kind"] for choice in listed[:-1]} == {"simulator"}
    assert listed[-1]["udid"] == PHONE_UDID and listed[-1]["connection"] == "usb"
    assert rig.phones is not None
    rig.phones.fail = DeviceControlError("devicectl is not installed")
    assert all(choice["kind"] == "simulator" for choice in await rig.manager.devices(TP1))
    rig.xcrun.on("simctl", "list", "devices", "-j", rc=1, err="CoreSimulator is not running")
    with pytest.raises(SimulatorUnavailable) as refused:
        await rig.manager.devices(TP1)
    assert refused.value.status == 502


async def test_a_picked_real_device_comes_up_without_booting_and_is_let_go_without_shutting_down(
    tmp_path: Path,
) -> None:
    rig = phone_rig(tmp_path)
    rig.config.set(max_booted=1)
    simulator = await rig.up("tp-2")
    await rig.manager.choose(TP1, PHONE_UDID)
    assert rig.manager.kind(TP1) == "physical"
    phone = await rig.up()
    assert phone.kind == "physical" and phone.connection == "usb" and phone.connector == "phone"
    assert phone.state == READY and simulator.state == READY, "a real device does not count against max_booted"
    assert rig.phones is not None and rig.phones.prepared == [PHONE_UDID]
    assert not any(args[1] == "boot" and args[2] == PHONE_UDID for args in rig.argv())
    assert rig.manager.control(phone) is rig.phones.control_for
    assert (await rig.manager.status(TP1))["device"]["kind"] == "physical"  # type: ignore[index]
    await rig.manager.stop(TP1, shutdown_device=True)
    assert phone.state == STOPPED and rig.phones.released == [(PHONE_UDID, True)]
    assert rig.manager.kind(TP1) == "physical", "the pick is kept"


async def test_a_remembered_real_device_that_is_gone_is_not_replaced_by_a_new_simulator(tmp_path: Path) -> None:
    rig = phone_rig(tmp_path)
    await rig.manager.choose(TP1, PHONE_UDID)
    assert rig.phones is not None
    rig.phones.phones.clear()
    with pytest.raises(SimulatorUnavailable) as refused:
        await rig.manager.ensure(TP1)
    assert refused.value.status == 409 and "is not connected" in str(refused.value)
    assert not any(args[1] == "create" for args in rig.argv())


async def test_a_real_device_needs_a_connector_that_drives_one_and_a_host_that_offers_them(tmp_path: Path) -> None:
    off = phone_rig(tmp_path / "off", phone=FakeConnector("phone", kinds=PHYSICAL, available=False))
    await off.manager.choose(TP1, PHONE_UDID)
    with pytest.raises(SimulatorUnavailable, match="No connector can reach a real device here"):
        await off.manager.ensure(TP1)
    assert (await off.manager.unavailable(TP1) or "").startswith("No connector can reach a real device here")
    plain = DeviceRig(tmp_path / "plain", phone=FakeConnector("phone", kinds=PHYSICAL))
    plain.manager.directory.memory.choose(TP1, False, PHONE_UDID)
    with pytest.raises(SimulatorUnavailable, match="Real devices are not available here"):
        await plain.manager.ensure(TP1)
    for udid, status, said in (
        ("not-a-udid", 400, "not a device id: 'not-a-udid'"),
        (PHONE_UDID, 409, "Real devices are not available here"),
        ("00008150-0099887766554433", 404, "That device is not connected to this Mac."),
    ):
        rig = plain if status != 404 else off
        with pytest.raises(SimulatorUnavailable) as refused:
            await rig.manager.choose(scope("tp-3"), udid)
        assert refused.value.status == status and said in str(refused.value)
    plain.xcrun.on("simctl", "list", "devices", "-j", rc=1, err="CoreSimulator is not running")
    with pytest.raises(SimulatorUnavailable) as unlisted:
        await plain.manager.choose(scope("tp-3"), BOOTED_UDID)
    assert unlisted.value.status == 400 and "CoreSimulator is not running" in str(unlisted.value)


async def test_a_session_says_what_it_can_do_whatever_the_probe_said(tmp_path: Path) -> None:
    more = VIEW_ONLY | {Capability.STREAM_H264}
    rig = phone_rig(
        tmp_path, phone=FakeConnector("phone", kinds=PHYSICAL, capabilities=VIEW_ONLY, session_capabilities=more)
    )
    await rig.manager.choose(TP1, PHONE_UDID)
    assert (await rig.up()).capabilities == more


def test_the_host_copy_names_each_kind() -> None:
    copy = HostCopy(area_off="Not here.")
    assert copy.kind_unavailable("simulator") == "Not here."
    assert copy.no_such_device("simulator") == "That simulator does not exist on this Mac."


async def test_a_simulator_backend_looks_up_and_controls_through_simctl() -> None:
    xcrun = FakeXcrun().with_lists()
    backend = SimulatorBackend(
        lambda developer_dir: Simctl(xcrun, developer_dir=developer_dir),
        DeviceDirectory(JsonDeviceMemory(Path("/nonexistent/devices.json"))),
    )
    config = SimConfig.defaults()
    found = await backend.lookup(BOOTED_UDID, config)
    assert found is not None and found["kind"] == "simulator" and found["usable"]
    assert await backend.lookup("00000000-0000-0000-0000-000000000000", config) is None
    control = backend.control("/Applications/Xcode27.app/Contents/Developer")
    assert isinstance(control, SimulatorControl)
    assert control.simctl.developer_dir == "/Applications/Xcode27.app/Contents/Developer"
