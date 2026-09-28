# SPDX-License-Identifier: Apache-2.0
"""A real device's backend and control, through devicectl as two real iPhones answered it: listed while real devices
are on, used only once connected and ready, never shut down, and driven the way a simulator is."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.core.backends import PhysicalBackend
from sim_mirror.core.control import DisplayState, NoDeviceLogs, PhysicalControl
from sim_mirror.core.devices import NoDevice
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.devicectl import Devicectl, DevicectlError
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun, fixture_json
from sim_mirror.testing.rig import scope

SECOND = "00008150-0099887766554433"
ON = SimConfig.defaults().with_values(real_devices=True, developer_dir="/X.app/Contents/Developer")


def backend(fake: FakeXcrun | None = None, **options: Any) -> tuple[PhysicalBackend, FakeXcrun]:
    fake = fake or FakeXcrun().with_devicectl()
    return PhysicalBackend(lambda xcode: Devicectl(fake, developer_dir=xcode), **options), fake


def instance(udid: str = PHONE_UDID) -> DeviceInstance:
    return DeviceInstance(
        udid=udid,
        name="Test iPhone",
        runtime="iOS 26.3",
        owner=scope("tp-1"),
        developer_dir="",
        scopes={"tp-1"},
        created=False,
        connector="iphone",
        capabilities=frozenset(),
        kind="physical",
    )


async def test_real_devices_are_listed_by_name_while_they_are_on() -> None:
    made, fake = backend()
    listed = await made.choices(ON, ())
    assert [(c["name"], c["runtime"], c["state"], c["connection"], c["usable"]) for c in listed] == [
        ("Second iPhone", "iOS 27.0 · iPhone 17 Pro", "Connected", "network", True),
        ("Test iPhone", "iOS 26.3 · iPhone 14 Pro", "Connected", "usb", True),
    ]
    assert all(c["kind"] == "physical" and not c["created"] and c["detail"] is None for c in listed)
    assert fake.calls[0].developer_dir == "/X.app/Contents/Developer"
    assert (await made.lookup(PHONE_UDID, ON)) == listed[1]
    assert await made.lookup("00008120-0000000000000000", ON) is None
    off = ON.with_values(real_devices=False)
    assert await made.choices(off, ()) == [] and await made.lookup(PHONE_UDID, off) is None
    assert made.developer_dir(ON.with_values(real_devices_developer_dir="/Y")) == "/Y"
    assert made.kind == "physical" and not made.counts_toward_max_booted


async def test_a_remembered_device_is_used_while_it_is_connected_and_ready_and_is_never_shut_down() -> None:
    made, _ = backend()
    ref = await made.resolve(scope("tp-1"), PHONE_UDID, ON)
    assert (ref.udid, ref.name, ref.runtime, ref.kind, ref.connection, ref.created) == (
        PHONE_UDID,
        "Test iPhone",
        "iOS 26.3 · iPhone 14 Pro",
        "physical",
        "usb",
        False,
    )
    assert (await made.resolve(scope("tp-1"), SECOND, ON)).connection == "network"
    running = instance()
    await made.prepare(running, ON)
    assert running.connection == "usb"
    assert await made.release(running, shutdown=True) is None
    assert isinstance(made.control("/X").devicectl, Devicectl) and isinstance(made.logs, NoDeviceLogs)


def _devices(**changes: Any) -> FakeXcrun:
    """devicectl's list with the cabled phone changed as a test says -- its new and deprecated keys alike."""
    listing = fixture_json("devicectl-devices.json")
    for device in listing["result"]["devices"]:
        if device["hardwareProperties"].get("udid") == PHONE_UDID:
            for key, value in changes.items():
                device["connectionProperties"][key] = value
                device["properties"]["connection"][{"tunnelState": "state"}.get(key, key)] = value
    return FakeXcrun().with_devicectl().on("devicectl", "-q", "list", "devices", out=json.dumps(listing))


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"tunnelState": "disconnected"}, "Test iPhone is not connected: plug it in and unlock it"),
        ({"pairingState": "unpaired"}, "Test iPhone: Not paired: unlock it and choose Trust."),
    ],
)
async def test_a_device_that_is_not_ready_is_refused_with_what_to_do(changes: dict[str, str], said: str) -> None:
    made, _ = backend(_devices(**changes))
    with pytest.raises(NoDevice, match=said):
        await made.resolve(scope("tp-1"), PHONE_UDID, ON)
    with pytest.raises(NoDevice, match=said):
        await made.prepare(instance(), ON)


async def test_a_device_that_is_gone_or_real_devices_off_is_refused() -> None:
    made, _ = backend()
    with pytest.raises(NoDevice, match="00008120-0000000000000000 is not connected"):
        await made.resolve(scope("tp-1"), "00008120-0000000000000000", ON)
    with pytest.raises(NoDevice, match="is not connected"):
        await made.resolve(scope("tp-1"), None, ON)
    with pytest.raises(NoDevice, match="Real devices are not available here"):
        await made.resolve(scope("tp-1"), PHONE_UDID, ON.with_values(real_devices=False))


async def test_a_device_is_driven_through_devicectl_the_way_a_simulator_is(tmp_path: Path) -> None:
    fake = FakeXcrun().with_devicectl()
    control = PhysicalControl(Devicectl(fake))
    await control.install(PHONE_UDID, "/tmp/Notes.app")
    assert await control.launch(PHONE_UDID, "com.apple.Preferences", terminate_running=True) == 718
    await control.openurl(PHONE_UDID, "notes://")
    await control.pbcopy(PHONE_UDID, "hi")
    await control.appearance(PHONE_UDID, "light")
    await control.text_size(PHONE_UDID, "large")
    await control.contrast(PHONE_UDID, True)
    await control.reduce_motion(PHONE_UDID, False)
    await control.locate(PHONE_UDID, 1, 2)
    await control.clear_location(PHONE_UDID)
    await control.clear_status_bar(PHONE_UDID)
    words = [call.args[2:5] for call in fake.calls]
    assert words == [
        ("device", "install", "app"),
        ("device", "process", "launch"),
        ("device", "process", "openURL"),
        ("device", "pasteboard", "copy"),
        ("device", "settings", "appearance"),
        ("device", "settings", "appearance"),
        ("device", "settings", "appearance"),
        ("device", "settings", "appearance"),
        ("device", "simulate", "location"),
        ("device", "simulate", "location"),
    ]
    assert fake.calls[4].args[-4:-2] == ("--mode", "light") and fake.calls[6].args[-4:-2] == (
        "--increase-contrast",
        "on",
    )
    assert fake.calls[7].args[-4:-2] == ("--reduce-motion", "off")
    assert await control.display(PHONE_UDID) == DisplayState("dark", "large", False, False)
    with pytest.raises(DevicectlError, match="shows 9:41 by itself"):
        await control.demo_status_bar(PHONE_UDID)
    with pytest.raises(DevicectlError, match="read over its cable"):
        await control.logs(PHONE_UDID, since_s=60, bundle_id=None)


async def test_a_route_is_given_to_devicectl_as_a_file_it_reads() -> None:
    seen: list[dict[str, Any]] = []

    class Routes(Devicectl):
        async def route(self, udid: str, route_file: Path) -> None:
            seen.append(json.loads(route_file.read_text()))

    await PhysicalControl(Routes(FakeXcrun())).route(PHONE_UDID, [(1.0, 2.0), (3.0, 4.0)], 12.5)
    assert seen == [
        {
            "mode": "interval",
            "interval": 1.0,
            "speed": 12.5,
            "waypoints": [{"latitude": 1.0, "longitude": 2.0}, {"latitude": 3.0, "longitude": 4.0}],
        }
    ]


async def test_an_app_is_quit_by_the_process_that_runs_from_its_bundle() -> None:
    notes = {
        "info": {"outcome": "success"},
        "result": {
            "apps": [
                {
                    "bundleIdentifier": "com.example.Notes",
                    "url": fixture_json("devicectl-apps.json")["result"]["apps"][0]["url"].rstrip("/"),
                }
            ]
        },
    }
    fake = FakeXcrun().with_devicectl().on("devicectl", "-q", "device", "info", "apps", out=json.dumps(notes))
    control = PhysicalControl(Devicectl(fake))
    assert await control.terminate(PHONE_UDID, "com.example.Notes")
    assert fake.calls[-1].args[2:9] == ("device", "process", "terminate", "--device", PHONE_UDID, "--pid", "4321")
    nothing = {"info": {"outcome": "success"}, "result": {"apps": []}}
    fake.on("devicectl", "-q", "device", "info", "apps", out=json.dumps(nothing))
    assert not await control.terminate(PHONE_UDID, "com.example.Gone")
    trips = FakeXcrun().with_devicectl()
    assert not await PhysicalControl(Devicectl(trips)).terminate(PHONE_UDID, "com.example.Trips")


async def test_a_devices_look_that_says_little_is_read_as_unknown() -> None:
    said = {"info": {"outcome": "success"}, "result": {"userInterfaceStyle": "unspecified", "textSize": 12}}
    fake = FakeXcrun().on("devicectl", "-q", "device", "info", "appearance", out=json.dumps(said))
    assert await PhysicalControl(Devicectl(fake)).display(PHONE_UDID) == DisplayState()


class Kept:
    def lines(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str] | None:
        return [f"{udid} {since_s} {bundle_id}"]


async def test_a_devices_log_is_what_is_kept_of_it() -> None:
    control = PhysicalControl(Devicectl(FakeXcrun()), Kept())
    assert await control.logs(PHONE_UDID, since_s=30, bundle_id="com.example.Notes") == [
        f"{PHONE_UDID} 30 com.example.Notes"
    ]
