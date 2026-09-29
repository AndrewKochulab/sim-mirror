# SPDX-License-Identifier: Apache-2.0
"""What SimMirror changes about a device is put back as it was, newest first -- and only when it is to be."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sim_mirror.connectors.base import Capability
from sim_mirror.core.control import DisplayState, SimulatorControl
from sim_mirror.core.device_changes import ChangeJournal, DeviceChanges, Left, undo
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.testing.fakes import BOOTED_UDID, PHONE_UDID, FakeConnector, FakeControl, FakePhoneBackend, FakeXcrun
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope

UDID = "U"


async def test_every_change_is_put_back_to_what_it_was_newest_first_and_only_the_first_is_remembered() -> None:
    control = FakeControl(display=DisplayState("light", "large", False, True))
    ledger = DeviceChanges()
    changes = ledger.on(control, UDID, remember=True)
    await changes.appearance("dark")
    await changes.appearance("light")
    await changes.text_size("extra-large")
    await changes.contrast(True)
    await changes.reduce_motion(False)
    await changes.status_bar(True)
    await changes.locate(51.5, -0.12)
    await changes.route([(1.0, 2.0), (3.0, 4.0)], 10)
    await changes.status_bar(False)
    await changes.clear_location()
    assert ledger.changed == ["appearance", "text_size", "contrast", "reduce_motion", "status_bar", "location"]
    assert [call for call in control.calls if call[0] == "display"] == [("display", UDID)], "read once"
    control.calls.clear()
    assert await ledger.restore(control, UDID) == []
    assert control.calls == [
        ("clear_location", UDID),
        ("clear_status_bar", UDID),
        ("reduce_motion", UDID, True),
        ("contrast", UDID, False),
        ("text_size", UDID, "large"),
        ("appearance", UDID, "light"),
    ]
    assert ledger.changed == [] and await ledger.restore(control, UDID) == []


async def test_what_is_not_to_be_put_back_is_only_changed_and_nothing_is_read_first() -> None:
    control = FakeControl()
    ledger = DeviceChanges()
    changes = ledger.on(control, UDID, remember=False)
    await changes.appearance("dark")
    await changes.status_bar(True)
    await changes.locate(1, 2)
    assert control.calls == [("appearance", UDID, "dark"), ("demo_status_bar", UDID), ("locate", UDID, 1, 2)]
    assert ledger.changed == []


async def test_a_setting_that_cannot_be_read_is_not_put_back_and_one_that_fails_leaves_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    control = FakeControl(display=DisplayState(appearance="dark"))
    ledger = DeviceChanges()
    changes = ledger.on(control, UDID, remember=True)
    await changes.text_size("small")
    await changes.appearance("light")
    await changes.locate(1, 2)
    assert ledger.changed == ["appearance", "location"]
    control.fail = DeviceControlError("the device went away")
    with caplog.at_level(logging.WARNING):
        assert await ledger.restore(control, UDID) == ["location", "appearance"]
    assert "could not put back the device's location: the device went away" in caplog.text


async def test_a_simulator_s_look_is_read_through_simctl() -> None:
    fake = (
        FakeXcrun()
        .on("simctl", "ui", BOOTED_UDID, "appearance", out="dark\n")
        .on("simctl", "ui", BOOTED_UDID, "content_size", out="unknown\n")
        .on("simctl", "ui", BOOTED_UDID, "increase_contrast", out="enabled\n")
    )
    control = SimulatorControl(Simctl(fake))
    assert await control.display(BOOTED_UDID) == DisplayState("dark", None, True, None)
    with pytest.raises(SimctlError, match="reduce motion cannot be set"):
        await control.reduce_motion(BOOTED_UDID, True)


async def test_a_real_device_gets_back_what_was_changed_and_a_simulator_only_when_asked(tmp_path: Path) -> None:
    phones = FakePhoneBackend()
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=VIEW_ONLY)
    rig = DeviceRig(tmp_path, phones=phones, phone=phone)
    await rig.manager.choose(scope("tp-1"), PHONE_UDID)
    instance = await rig.up()
    await rig.manager.changes(instance).appearance("dark")
    await rig.manager.stop(scope("tp-1"))
    assert phones.control_for.calls[-1] == ("appearance", PHONE_UDID, "light")
    simulator = await rig.up("tp-2")
    await rig.manager.changes(simulator).appearance("dark")
    await rig.manager.stop(scope("tp-2"))
    assert ("simctl", "ui", simulator.udid, "appearance") not in rig.argv(), "nothing read, nothing put back"
    rig.config.set(restore_changes="all")
    again = await rig.up("tp-2")
    rig.xcrun.on("simctl", "ui", again.udid, "appearance", out="light\n")
    await rig.manager.changes(again).appearance("dark")
    await rig.manager.stop(scope("tp-2"))
    assert rig.argv()[-1] == ("simctl", "ui", again.udid, "appearance", "light")


async def test_the_demo_status_bar_setting_gives_every_device_one_while_it_is_driven(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(demo_status_bar="demo")
    instance = await rig.up()
    assert instance.demo_status_bar and ("simctl", "status_bar", instance.udid, "override") == rig.argv()[-1][:4]
    await rig.manager.stop(scope("tp-1"))
    assert ("simctl", "status_bar", instance.udid, "clear") in rig.argv() and not instance.demo_status_bar
    rig.xcrun.on("simctl", "status_bar", rc=1, err="status bar overrides are not supported")
    with caplog.at_level(logging.WARNING):
        refused = await rig.up()
    assert not refused.demo_status_bar and "could not give" in caplog.text
    refused.demo_status_bar = True
    await rig.manager.stop(scope("tp-1"))
    assert "its own status bar back" in caplog.text
    plain = DeviceRig(tmp_path / "plain", idb=FakeConnector("idb", capabilities=VIEW_ONLY - {Capability.STATUS_BAR}))
    plain.config.set(demo_status_bar="demo")
    assert not (await plain.up()).demo_status_bar


async def test_what_is_remembered_is_written_down_and_what_fails_to_go_back_stays_written(tmp_path: Path) -> None:
    journal = ChangeJournal(tmp_path / "run" / "device-changes.json")
    assert journal.pending() == {}
    control = FakeControl(display=DisplayState("light", "large", False, True))
    ledger = DeviceChanges()
    changes = ledger.on(control, PHONE_UDID, remember=True, journal=journal, developer_dir="/X.app")
    await changes.text_size("extra-large")
    await changes.locate(1, 2)
    assert journal.pending() == {PHONE_UDID: Left("/X.app", {"text_size": "large", "location": True})}
    assert (tmp_path / "run" / "device-changes.json").stat().st_mode & 0o777 == 0o600
    control.fail = DeviceControlError("unplugged")
    assert await ledger.restore(control, PHONE_UDID) == ["location", "text_size"]
    assert journal.pending()[PHONE_UDID].changes == {"text_size": "large", "location": True}
    control.fail = None
    assert await undo(control, PHONE_UDID, {"text_size": "large", "unknown": 1}) == []
    journal.write(PHONE_UDID, Left("/X.app"))
    assert journal.pending() == {}
    memory = ChangeJournal()
    memory.write(UDID, Left("", {"appearance": "dark"}))
    assert memory.pending() == {UDID: Left("", {"appearance": "dark"})}
    (tmp_path / "run" / "device-changes.json").write_text('{"x": {"changes": 1}, "y": "no"}')
    assert journal.pending() == {}
    (tmp_path / "run" / "device-changes.json").write_text("not json")
    assert journal.pending() == {}


async def test_a_start_after_a_crash_puts_back_what_the_last_run_left_changed(tmp_path: Path) -> None:
    phones = FakePhoneBackend()
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=VIEW_ONLY)
    journal = ChangeJournal(tmp_path / "left.json")
    journal.write(PHONE_UDID, Left("/X.app", {"text_size": "large", "appearance": "light"}))
    journal.write("not-a-device", Left("", {"appearance": "dark"}))
    rig = DeviceRig(tmp_path, phones=phones, phone=phone, journal=journal)
    await rig.manager.start_at_boot()
    assert phones.control_for.calls[-2:] == [("appearance", PHONE_UDID, "light"), ("text_size", PHONE_UDID, "large")]
    assert list(journal.pending()) == ["not-a-device"], "one that is no device is left for a person to look at"
    journal.write(PHONE_UDID, Left("/X.app", {"location": True}))
    phones.control_for.fail = DeviceControlError("not connected")
    assert await rig.manager.put_back_left() == []
    assert journal.pending()[PHONE_UDID].changes == {"location": True}, "kept for the next start"
