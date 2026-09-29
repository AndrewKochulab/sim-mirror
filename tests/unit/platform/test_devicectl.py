# SPDX-License-Identifier: Apache-2.0
"""devicectl, read from its JSON as real iPhones printed it: new keys or deprecated ones, and what went wrong said as
devicectl said it."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.platform.devicectl import Devicectl, DevicectlError, read_device, text_size_name
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun, fixture_json

SECOND = "00008150-0099887766554433"
XCODE = "/Applications/Xcode27.app/Contents/Developer"


def make(fake: FakeXcrun | None = None) -> tuple[Devicectl, FakeXcrun]:
    fake = fake or FakeXcrun().with_devicectl()
    return Devicectl(fake, developer_dir=XCODE), fake


async def test_the_real_devices_are_listed_with_how_they_are_connected_and_what_they_can_do() -> None:
    devicectl, fake = make()
    second, phone = await devicectl.devices()
    assert (phone.udid, phone.name, phone.model, phone.platform, phone.os_version) == (
        PHONE_UDID,
        "Test iPhone",
        "iPhone 14 Pro",
        "iOS",
        "26.3",
    )
    assert (phone.connection, phone.detail, phone.developer_mode) == ("usb", None, True)
    assert {"capturescreenshot", "customizeuistyle", "simulatelocation"} <= phone.features
    assert (second.udid, second.connection, second.os_version) == (SECOND, "network", "27.0")
    assert fake.calls[0].args == ("devicectl", "-q", "list", "devices", "--json-output", "-")
    assert fake.calls[0].developer_dir == XCODE
    assert (await devicectl.device(PHONE_UDID)) == phone
    assert await devicectl.device("00008120-0000000000000000") is None


def _entry(**changes: Any) -> dict[str, Any]:
    """The cabled phone's entry with only its deprecated keys, changed as a test says."""
    entry = next(
        device
        for device in fixture_json("devicectl-devices.json")["result"]["devices"]
        if device["hardwareProperties"].get("udid") == PHONE_UDID
    )
    entry = {key: value for key, value in entry.items() if key != "properties"}
    for key, value in changes.items():
        section, field = key.split("__")
        entry[section] = {**entry[section], field: value}
    return entry


@pytest.mark.parametrize(
    ("changes", "connection", "detail", "developer_mode"),
    [
        ({}, "usb", None, True),
        ({"connectionProperties__pairingState": "unpaired"}, "usb", "Not paired: unlock it and choose Trust", True),
        ({"connectionProperties__transportType": None}, None, "Not connected", True),
        ({"deviceProperties__developerModeStatus": "disabled"}, "usb", "Developer Mode off", False),
        ({"deviceProperties__developerModeStatus": None}, "usb", None, None),
        ({"connectionProperties__transportType": "localNetwork"}, "network", None, True),
    ],
)
def test_a_device_read_from_the_deprecated_keys_alone_says_why_it_cannot_be_used(
    changes: dict[str, Any], connection: str | None, detail: str | None, developer_mode: bool | None
) -> None:
    device = read_device(_entry(**changes))
    assert device is not None
    assert (device.connection, device.detail, device.developer_mode) == (connection, detail, developer_mode)


def test_what_is_not_a_real_iphone_or_ipad_is_left_out() -> None:
    assert read_device(_entry(hardwareProperties__reality="simulated")) is None
    assert read_device(_entry(hardwareProperties__platform="watchOS")) is None
    assert read_device(_entry(hardwareProperties__udid="not-a-udid")) is None
    assert read_device("nonsense") is None
    nameless = read_device(_entry(deviceProperties__name=None, hardwareProperties__marketingName=None))
    assert nameless is not None and (nameless.name, nameless.model) == (PHONE_UDID, "iPhone")
    developer = _entry()
    developer["properties"] = {"state": {"developerModeStatus": {}}}
    assert read_device(developer).developer_mode is None  # type: ignore[union-attr]


async def test_the_screen_lock_appearance_apps_and_processes_are_read() -> None:
    devicectl, fake = make()
    display = await devicectl.display(PHONE_UDID)
    assert (display.width_px, display.height_px, display.scale, display.orientation) == (1179, 2556, 3.0, "portrait")
    lock = await devicectl.lock_state(PHONE_UDID)
    assert (lock.passcode_required, lock.unlocked_since_boot) == (False, True)
    appearance = await devicectl.appearance(PHONE_UDID)
    assert (appearance["userInterfaceStyle"], appearance["textSize"]) == ("dark", "Large")
    apps = await devicectl.apps(PHONE_UDID)
    assert [app.bundle_id for app in apps] == ["com.example.Notes", "com.example.Trips"]
    await devicectl.apps(PHONE_UDID, bundle_id="com.apple.Preferences")
    assert fake.calls[-1].args[-5:] == (
        "--bundle-id",
        "com.apple.Preferences",
        "--include-default-apps",
        "--json-output",
        "-",
    )
    processes = await devicectl.processes(PHONE_UDID)
    assert [(process.pid, process.executable.rsplit("/", 1)[-1]) for process in processes] == [
        (718, "Preferences"),
        (4321, "Notes"),
        (42, "lockdownd"),
    ]


async def test_apps_are_installed_launched_quit_and_opened_and_text_reaches_the_pasteboard(tmp_path: Path) -> None:
    devicectl, fake = make()
    await devicectl.install(PHONE_UDID, "/tmp/Build/Notes.app")
    assert await devicectl.launch(PHONE_UDID, "com.apple.Preferences", ["-UITest", "1"]) == 718
    await devicectl.launch(PHONE_UDID, "com.apple.Preferences", terminate_running=True)
    await devicectl.terminate(PHONE_UDID, 718)
    await devicectl.open_url(PHONE_UDID, "notes://new")
    await devicectl.pbcopy(PHONE_UDID, "café 😀")
    await devicectl.uninstall(PHONE_UDID, "com.example.Notes")
    await devicectl.screenshot(PHONE_UDID, tmp_path / "shot.png")
    assert [call.args[2:-2] for call in fake.calls] == [
        ("device", "install", "app", "--device", PHONE_UDID, "/tmp/Build/Notes.app"),
        ("device", "process", "launch", "--device", PHONE_UDID, "com.apple.Preferences", "-UITest", "1"),
        ("device", "process", "launch", "--device", PHONE_UDID, "--terminate-existing", "com.apple.Preferences"),
        ("device", "process", "terminate", "--device", PHONE_UDID, "--pid", "718"),
        ("device", "process", "openURL", "--device", PHONE_UDID, "notes://new"),
        ("device", "pasteboard", "copy", "--device", PHONE_UDID),
        ("device", "uninstall", "app", "--device", PHONE_UDID, "com.example.Notes"),
        ("device", "capture", "screenshot", "--device", PHONE_UDID, "--destination", str(tmp_path / "shot.png")),
    ]
    assert fake.calls[0].timeout == 600.0 and fake.calls[5].input_data == "café 😀".encode()


async def test_how_a_device_looks_and_where_it_is_are_changed(tmp_path: Path) -> None:
    devicectl, fake = make()
    await devicectl.set_appearance(PHONE_UDID, mode="dark", text_size="extra-large")
    await devicectl.set_appearance(PHONE_UDID, increase_contrast="on", reduce_motion="off")
    await devicectl.locate(PHONE_UDID, 51.5, -0.12)
    await devicectl.route(PHONE_UDID, tmp_path / "route.json")
    await devicectl.clear_location(PHONE_UDID)
    assert [call.args[2:-2] for call in fake.calls] == [
        ("device", "settings", "appearance", "--device", PHONE_UDID, "--mode", "dark", "--text-size", "extra-large"),
        (
            "device",
            "settings",
            "appearance",
            "--device",
            PHONE_UDID,
            "--increase-contrast",
            "on",
            "--reduce-motion",
            "off",
        ),
        (
            "device",
            "simulate",
            "location",
            "coordinate",
            "--device",
            PHONE_UDID,
            "--latitude",
            "51.500000",
            "--longitude",
            "-0.120000",
        ),
        (
            "device",
            "simulate",
            "location",
            "route",
            "--device",
            PHONE_UDID,
            "--route-file",
            str(tmp_path / "route.json"),
        ),
        ("device", "simulate", "location", "clear", "--device", PHONE_UDID),
    ]
    assert text_size_name("Accessibility Extra Large") == "accessibility-extra-large"
    assert text_size_name("Large") == "large" and text_size_name("Huge") is None and text_size_name(3) is None


Call = Callable[[Devicectl], Awaitable[object]]


@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.display("D946616B-6E4F-4F5C-8C76-54FAD9B7D702"),
        lambda d: d.launch(PHONE_UDID, "--flag"),
        lambda d: d.launch(PHONE_UDID, "com.acme.Notes", ["ok\x00"]),
        lambda d: d.install(PHONE_UDID, "-rf"),
        lambda d: d.terminate(PHONE_UDID, 0),
        lambda d: d.terminate(PHONE_UDID, True),
        lambda d: d.set_appearance(PHONE_UDID, mode="sepia"),
        lambda d: d.set_appearance(PHONE_UDID, volume="11"),
        lambda d: d.set_appearance(PHONE_UDID),
        lambda d: d.locate(PHONE_UDID, 91, 0),
        lambda d: d.apps(PHONE_UDID, bundle_id="not a bundle"),
    ],
)
async def test_nothing_that_could_read_as_an_option_reaches_devicectl(call: Call) -> None:
    devicectl, fake = make()
    with pytest.raises(DevicectlError):
        await call(devicectl)
    assert fake.calls == []


async def test_what_went_wrong_is_said_as_devicectl_said_it() -> None:
    missing = (
        FakeXcrun()
        .with_devicectl()
        .on(
            "devicectl",
            "-q",
            "device",
            "process",
            "launch",
            rc=1,
            out=json.dumps(fixture_json("devicectl-launch-missing.json")),
        )
    )
    devicectl, _ = make(missing)
    with pytest.raises(DevicectlError) as refused:
        await devicectl.launch(PHONE_UDID, "com.nowhere.NotInstalled")
    assert str(refused.value) == (
        "devicectl device process launch: The application failed to launch. The requested application "
        "com.nowhere.NotInstalled is not installed. Provide a valid bundle identifier."
    )
    assert refused.value.result is not None and refused.value.result.rc == 1
    garbled = FakeXcrun().on("devicectl", rc=1, err='xcrun: error: unable to find utility "devicectl"')
    with pytest.raises(DevicectlError, match='unable to find utility "devicectl"'):
        await Devicectl(garbled).devices()
    silent = FakeXcrun().on("devicectl", rc=1)
    with pytest.raises(DevicectlError, match="xcrun exited with 1"):
        await Devicectl(silent).devices()
    unreadable = FakeXcrun().on("devicectl", rc=1, out="devicectl: not json")
    with pytest.raises(DevicectlError, match="devicectl list devices: devicectl: not json"):
        await Devicectl(unreadable).devices()
    no_screen = FakeXcrun().on(
        "devicectl", out=json.dumps({"info": {"outcome": "success"}, "result": {"displays": []}})
    )
    with pytest.raises(DevicectlError, match="did not say how large"):
        await Devicectl(no_screen).display(PHONE_UDID)
    assert Devicectl(FakeXcrun(), developer_dir="/X").developer_dir == "/X"
