# SPDX-License-Identifier: Apache-2.0
"""An Android device's tool: apps, the log, how it looks -- read exactly and put back so -- and where it is."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sim_mirror.build.stamp import APK_STAMP, Stamp, stamp_apk, stamp_of
from sim_mirror.core.android_control import (
    FONT_SCALES,
    STAMPS,
    AndroidControl,
    launch_extras,
    nearest_text_size,
    route_fixes,
    stop_routes,
)
from sim_mirror.core.control import DeviceControl, DisplayState, ExactRestore
from sim_mirror.platform.adb import AdbError
from sim_mirror.platform.emulator import EmulatorConsole
from sim_mirror.testing.adb import FakeAdbServer, FakeEmulatorConsole
from sim_mirror.testing.fakes import fixture

UDID = "avd-Pixel-9-3fa2c1"


class Rig:
    def __init__(self, server: FakeAdbServer, console: FakeEmulatorConsole, serial: str) -> None:
        self.server = server
        self.console = console
        self.slept: list[float] = []
        self.routes: dict[str, asyncio.Task[None]] = {}

        async def serial_of(udid: str) -> str:
            if udid != UDID:
                raise AdbError(f"{udid} is not running")
            return serial

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        def console_for(port: int) -> EmulatorConsole:
            return EmulatorConsole(console.port, token=lambda: console.token)

        self.control = AndroidControl(server.adb(), serial_of, routes=self.routes, console_for=console_for, sleep=sleep)

    def commands(self) -> list[str]:
        return self.server.commands()


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async with FakeAdbServer() as server, FakeEmulatorConsole() as console:
        server.device("emulator-5554", transport_id="1")
        yield Rig(server, console, "emulator-5554")


@pytest.fixture
async def phone() -> AsyncIterator[Rig]:
    async with FakeAdbServer() as server, FakeEmulatorConsole() as console:
        server.device("R58M12ABC", transport_id="2")
        yield Rig(server, console, "R58M12ABC")


def test_an_android_control_is_a_device_control_that_puts_back_exactly(rig: Rig) -> None:
    control: DeviceControl = rig.control
    assert isinstance(control, ExactRestore)


def test_launch_arguments_are_string_extras_of_the_launch_intent() -> None:
    assert launch_extras(["user=ada", "debug.level=2", "empty="]) == [
        "--es", "user", "ada", "--es", "debug.level", "2", "--es", "empty", "",
    ]  # fmt: skip
    with pytest.raises(AdbError, match="'--verbose' is not an Android launch argument"):
        launch_extras(["--verbose"])


def test_ios_text_sizes_are_font_scales_and_a_scale_is_read_as_the_nearest_size() -> None:
    assert FONT_SCALES["large"] == "1.0" and FONT_SCALES["accessibility-extra-extra-extra-large"] == "2.0"
    assert nearest_text_size("1.0") == "large"
    assert nearest_text_size("1.17") == "extra-large"
    assert nearest_text_size("loud") is None


def test_a_route_is_a_fix_a_second_along_its_waypoints() -> None:
    fixes = route_fixes([(52.5200, 13.4050), (52.5200, 13.4064)], 50.0)
    # About 95 m at 50 m a second: two steps, the start first and the end last.
    assert fixes[0] == (52.52, 13.405) and fixes[-1] == (52.52, 13.4064) and len(fixes) == 3
    assert route_fixes([(1.0, 1.0)], 10.0) == [(1.0, 1.0)]


async def test_an_apk_or_a_folder_of_splits_is_installed(rig: Rig, tmp_path: Path) -> None:
    rig.server.on("cmd package install -r -t -S", out="Success\n")
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"APK")
    await rig.control.install(UDID, str(apk))
    splits = tmp_path / "splits"
    splits.mkdir()
    for name in ("base.apk", "split_config.en.apk"):
        (splits / name).write_bytes(name.encode())
    rig.server.on("cmd package install-create", out="Success: created install session [7]\n")
    rig.server.on("cmd package install-write", out="Success\n").on("cmd package install-commit", out="Success\n")
    await rig.control.install(UDID, str(splits))
    assert rig.commands()[-1] == "cmd package install-commit 7"
    with pytest.raises(AdbError, match="holds no APK"):
        await rig.control.install(UDID, str(tmp_path / "splits" / "nothing"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(AdbError, match="holds no APK"):
        await rig.control.install(UDID, str(tmp_path / "empty"))


async def test_an_installed_app_is_read_from_the_package_manager(rig: Rig) -> None:
    rig.server.on("dumpsys package com.android.settings", out=fixture("android-package.txt"))
    rig.server.on("dumpsys package com.example.nothing", out=fixture("android-package-missing.txt"))
    app = await rig.control.app_info(UDID, "com.android.settings")
    assert app is not None and (app.bundle_id, app.name, app.version, app.location) == (
        "com.android.settings", "settings", "16 (36)", "/system_ext/priv-app/SettingsGoogle",
    )  # fmt: skip
    assert app.info == {"versionName": "16", "versionCode": "36", "lastUpdateTime": "2026-10-25 13:53:08"}
    assert await rig.control.app_info(UDID, "com.example.nothing") is None


async def test_an_emulator_keeps_a_stamped_apks_commit_until_the_app_is_installed_again(
    rig: Rig, tmp_path: Path
) -> None:
    built = tmp_path / "debug" / "app-debug.apk"
    built.parent.mkdir()
    built.write_bytes(b"APK")
    mark = Stamp("a" * 40, dirty=True)
    folder = stamp_apk(built, mark, "dev.simmirror.example")
    rig.server.on("cmd package install -r -t -S", out="Success\n")
    rig.server.on("dumpsys package dev.simmirror.example", out=fixture("android-package.txt"))
    await rig.control.install(UDID, str(folder))
    kept = rig.server.files[("emulator-5554", f"{STAMPS}/dev.simmirror.example.json")]
    assert json.loads(kept) == {"commit": "a" * 40, "dirty": True, "installed": "2026-10-25 13:53:08"}
    rig.server.on(f"cat {STAMPS}/dev.simmirror.example.json", out=kept)
    app = await rig.control.app_info(UDID, "dev.simmirror.example")
    assert app is not None and app.info is not None and stamp_of(app.info) == mark
    # Installed again since, by anything: the install time changed, and the stamp is not that app's.
    later = fixture("android-package.txt").replace("lastUpdateTime=2026-10-25 13:53:08", "lastUpdateTime=2026-10-26")
    rig.server.on("dumpsys package dev.simmirror.example", out=later)
    app = await rig.control.app_info(UDID, "dev.simmirror.example")
    assert app is not None and app.info is not None and stamp_of(app.info) is None
    # A package name that could not be a path's last part is never looked up.
    rig.server.on("dumpsys package example", out=fixture("android-package.txt"))
    app = await rig.control.app_info(UDID, "example")
    assert app is not None and app.info is not None and stamp_of(app.info) is None
    for said in (b"not json", b"[]"):
        rig.server.on(f"cat {STAMPS}/dev.simmirror.example.json", out=said)
        app = await rig.control.app_info(UDID, "dev.simmirror.example")
        assert app is not None and app.info is not None and stamp_of(app.info) is None


async def test_a_folder_without_a_stamp_installs_its_apk_and_keeps_nothing(rig: Rig, tmp_path: Path) -> None:
    rig.server.on("cmd package install -r -t -S", out="Success\n")
    (tmp_path / "app-debug.apk").write_bytes(b"APK")
    (tmp_path / APK_STAMP).write_text(json.dumps({"package": "../escape", "commit": "a" * 40}))
    await rig.control.install(UDID, str(tmp_path))
    (tmp_path / APK_STAMP).unlink()
    await rig.control.install(UDID, str(tmp_path))
    assert rig.server.files == {}


async def test_a_phones_app_is_never_stamped_and_says_nothing_of_one(tmp_path: Path) -> None:
    async with FakeAdbServer() as server:
        server.phone()
        server.on("cmd package install -r -t -S", out="Success\n")
        server.on("dumpsys package dev.simmirror.example", out=fixture("android-package.txt"))

        async def serial_of(udid: str) -> str:
            return "R58M12ABC"

        control = AndroidControl(server.adb(), serial_of)
        built = tmp_path / "debug" / "app-debug.apk"
        built.parent.mkdir()
        built.write_bytes(b"APK")
        await control.install("adb-R58M12ABC-9b1e04", str(stamp_apk(built, Stamp("b" * 40), "dev.simmirror.example")))
        app = await control.app_info("adb-R58M12ABC-9b1e04", "dev.simmirror.example")
        assert server.files == {} and app is not None and app.info is None


async def test_an_app_is_launched_by_its_launcher_activity_and_its_pid_read(rig: Rig) -> None:
    rig.server.on("cmd package resolve-activity", out="priority=0 preferredOrder=0\ncom.android.settings/.Settings\n")
    rig.server.on("am start", out=fixture("android-am-start.txt")).on("pidof", out="4242\n")
    assert await rig.control.launch(UDID, "com.android.settings", ["mode=demo"], terminate_running=True) == 4242
    assert rig.commands()[-3:] == [
        "am force-stop com.android.settings",
        "am start -W -n com.android.settings/.Settings --es mode demo",
        "pidof com.android.settings",
    ]
    rig.server.on("pidof", code=1)
    assert await rig.control.launch(UDID, "com.android.settings") is None


async def test_an_app_that_cannot_be_launched_says_why(rig: Rig) -> None:
    rig.server.on("cmd package resolve-activity", out="No activity found\n")
    with pytest.raises(AdbError, match="has no activity to launch"):
        await rig.control.launch(UDID, "com.example.nothing")
    rig.server.on("cmd package resolve-activity", out="")
    with pytest.raises(AdbError, match="has no activity to launch"):
        await rig.control.launch(UDID, "com.example.nothing")
    rig.server.on("cmd package resolve-activity", out="com.example.nothing/.Main\n")
    rig.server.on("am start", out=fixture("android-am-start-bad.txt"))
    with pytest.raises(AdbError, match=r"did not start: Activity class \{com.example.nothing"):
        await rig.control.launch(UDID, "com.example.nothing")


async def test_an_app_is_ended_saying_whether_it_ran_and_a_url_is_opened_as_a_view_intent(rig: Rig) -> None:
    rig.server.on("pidof", out="4242\n").on("am start", out="Status: ok\nComplete\n")
    assert await rig.control.terminate(UDID, "com.android.settings") is True
    rig.server.on("pidof", code=1)
    assert await rig.control.terminate(UDID, "com.android.settings") is False
    await rig.control.openurl(UDID, "https://example.com/a?b=1&c=2")
    assert rig.commands()[-1] == "am start -W -a android.intent.action.VIEW -d 'https://example.com/a?b=1&c=2'"
    rig.server.on("am start", out="Error: Activity not started, unable to resolve Intent\n")
    with pytest.raises(AdbError, match="unable to resolve Intent"):
        await rig.control.openurl(UDID, "nothing://here")


async def test_nothing_is_pasted_on_an_android_device(rig: Rig) -> None:
    with pytest.raises(AdbError, match="types text whole"):
        await rig.control.pbcopy(UDID, "hello")


async def test_the_log_is_an_apps_by_its_uid_or_the_errors_from_the_devices_own_clock_back(rig: Rig) -> None:
    rig.server.on("date", out="1792931977\n").on("pm list packages -U", out=fixture("android-pm-uid.txt"))
    rig.server.on("logcat", out=fixture("android-logcat-uid.txt"))
    lines = await rig.control.logs(UDID, since_s=60, bundle_id="com.android.settings")
    assert lines and all(line.split()[2] == "1000" for line in lines)
    assert not any(line.startswith("---------") for line in lines)
    assert rig.commands()[-1] == "logcat -d -v year -v uid -T 1792931917.000"
    rig.server.on("logcat", out="--------- beginning of crash\n2026-10-25 14:30:04.765  1835  2285 E fkpy: oops\n")
    assert await rig.control.logs(UDID, since_s=600, bundle_id=None) == [
        "2026-10-25 14:30:04.765  1835  2285 E fkpy: oops"
    ]
    assert rig.commands()[-1] == "logcat -d -v year -T 1792931377.000 '*:E'"
    rig.server.on("date", out="Sat Oct 25\n")
    await rig.control.logs(UDID, since_s=60, bundle_id=None)
    assert rig.commands()[-1] == "logcat -d -v year '*:E'"
    with pytest.raises(AdbError, match=r"com\.example\.nothing is not installed"):
        await rig.control.logs(UDID, since_s=60, bundle_id="com.example.nothing")


async def test_how_the_device_looks_is_read_and_set_through_its_settings(rig: Rig) -> None:
    rig.server.on("cmd uimode night", out="Night mode: yes\n")
    rig.server.on("settings get system font_scale", out="1.15\n")
    rig.server.on("settings get secure high_text_contrast_enabled", out="1\n")
    rig.server.on("settings get global animator_duration_scale", out="0.0\n")
    assert await rig.control.display(UDID) == DisplayState("dark", "extra-large", True, True)
    rig.server.on("settings get system font_scale", out="null\n").on("cmd uimode night", out="Night mode: auto\n")
    assert (await rig.control.display(UDID)).text_size == "large"
    await rig.control.appearance(UDID, "light")
    await rig.control.text_size(UDID, "extra-extra-large")
    await rig.control.contrast(UDID, False)
    await rig.control.reduce_motion(UDID, True)
    assert rig.commands()[-6:] == [
        "cmd uimode night no",
        "settings put system font_scale 1.3",
        "settings put secure high_text_contrast_enabled 0",
        "settings put global animator_duration_scale 0",
        "settings put global transition_animation_scale 0",
        "settings put global window_animation_scale 0",
    ]
    with pytest.raises(AdbError, match="huge is not a text size"):
        await rig.control.text_size(UDID, "huge")


async def test_a_demo_status_bar_is_systemuis_demo_mode(rig: Rig) -> None:
    await rig.control.demo_status_bar(UDID)
    await rig.control.clear_status_bar(UDID)
    assert rig.commands() == [
        "settings put global sysui_demo_allowed 1",
        "am broadcast -a com.android.systemui.demo -e command enter",
        "am broadcast -a com.android.systemui.demo -e command clock -e hhmm 0941",
        "am broadcast -a com.android.systemui.demo -e command battery -e level 100 -e plugged false",
        "am broadcast -a com.android.systemui.demo -e command network -e wifi show -e level 4 -e mobile show -e "
        "datatype none",
        "am broadcast -a com.android.systemui.demo -e command notifications -e visible false",
        "am broadcast -a com.android.systemui.demo -e command exit",
    ]


async def test_what_each_change_replaces_is_read_and_put_back_exactly(rig: Rig) -> None:
    rig.server.on("cmd uimode night", out="Night mode: custom_bedtime\n")
    rig.server.on("settings get system font_scale", out="1.15\n")
    rig.server.on("settings get secure high_text_contrast_enabled", out="null\n")
    rig.server.on("settings get global", out="0.5\n")
    rig.server.on("settings get system accelerometer_rotation", out="1\n")
    rig.server.on("settings get system user_rotation", out="0\n")
    saved = {what: await rig.control.saved(UDID, what) for what in
             ("appearance", "text_size", "contrast", "reduce_motion", "status_bar", "rotation")}  # fmt: skip
    assert saved == {
        "appearance": "custom_bedtime",
        "text_size": "1.15",
        "contrast": "null",
        "reduce_motion": {
            "animator_duration_scale": "0.5", "transition_animation_scale": "0.5", "window_animation_scale": "0.5",
        },
        "status_bar": "0.5",
        "rotation": {"accelerometer_rotation": "1", "user_rotation": "0"},
    }  # fmt: skip
    start = len(rig.commands())
    for what, value in saved.items():
        await rig.control.put_back(UDID, what, value)
    assert rig.commands()[start:] == [
        "cmd uimode night custom_bedtime",
        "settings put system font_scale 1.15",
        "settings delete secure high_text_contrast_enabled",
        "settings put global animator_duration_scale 0.5",
        "settings put global transition_animation_scale 0.5",
        "settings put global window_animation_scale 0.5",
        "am broadcast -a com.android.systemui.demo -e command exit",
        "settings put global sysui_demo_allowed 0.5",
        # How it lay first, then whether it follows the device again.
        "settings put system user_rotation 0",
        "settings put system accelerometer_rotation 1",
    ]
    with pytest.raises(AdbError, match="is not something SimMirror changes"):
        await rig.control.saved(UDID, "wallpaper")
    with pytest.raises(AdbError, match="cannot be put back"):
        await rig.control.put_back(UDID, "rotation", "sideways")


async def test_the_screen_is_turned_and_kept_turned(rig: Rig) -> None:
    rig.server.on("settings get system user_rotation", out="3\n")
    assert await rig.control.rotation(UDID) == 3
    rig.server.on("settings get system user_rotation", out="null\n")
    assert await rig.control.rotation(UDID) == 0
    await rig.control.set_rotation(UDID, 5)
    assert rig.commands()[-2:] == [
        "settings put system accelerometer_rotation 0",
        "settings put system user_rotation 1",
    ]


async def test_an_emulator_is_put_somewhere_and_moved_along_a_route_until_it_is_cleared(rig: Rig) -> None:
    await rig.control.locate(UDID, 52.52, 13.405)
    assert rig.console.commands == ["geo fix 13.405000 52.520000"]
    await rig.control.route(UDID, [(52.52, 13.405), (52.52, 13.4064)], 50)
    task = rig.routes[UDID]
    await task
    assert rig.console.commands[1:] == [
        "geo fix 13.405000 52.520000", "geo fix 13.405700 52.520000", "geo fix 13.406400 52.520000",
    ]  # fmt: skip
    assert rig.slept == [1.0, 1.0]
    started = asyncio.Event()

    async def slow(seconds: float) -> None:
        started.set()
        await asyncio.sleep(10)

    rig.control._sleep = slow
    await rig.control.route(UDID, [(1.0, 1.0), (2.0, 2.0)], 1)
    await started.wait()
    running = rig.routes[UDID]
    await rig.control.clear_location(UDID)
    with pytest.raises(asyncio.CancelledError):
        await running
    assert UDID not in rig.routes
    await rig.control.route(UDID, [(1.0, 1.0), (2.0, 2.0)], 1)
    await stop_routes(rig.routes)
    assert rig.routes == {}


async def test_a_phones_location_is_not_adbs_to_set(phone: Rig) -> None:
    with pytest.raises(AdbError, match="A phone's location cannot be set through adb"):
        await phone.control.locate(UDID, 1, 2)
