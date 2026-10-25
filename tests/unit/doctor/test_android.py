# SPDX-License-Identifier: Apache-2.0
"""The doctor's Android checks -- the SDK, the devices, scrcpy, Gradle's JDK -- and a Mac whose Android devices work
without Xcode, where a missing Xcode is a warning."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.doctor.android import check_android_devices, check_android_sdk, check_gradle, check_scrcpy
from sim_mirror.doctor.checks import INSTALL_XCODE, DoctorContext, check_connectors, check_xcode
from sim_mirror.doctor.report import CheckResult
from sim_mirror.platform.adb import Adb
from sim_mirror.platform.android_sdk import AndroidSdk, avds
from sim_mirror.platform.java import Jdk
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.android_sdk import avd, image, sdk_at
from sim_mirror.testing.fakes import FakeConnector
from sim_mirror.testing.scrcpy import fetched_jars

ON = SimConfig.defaults().with_values(android=True, android_phones=True, build_tools=True)
JDK = Jdk(Path("/opt/jdk"), "android.java_home")


async def no_xcode(argv: Sequence[str]) -> tuple[int, str]:
    return 1, ""


def context(
    root: Path, *, sdk: AndroidSdk | None, server: FakeAdbServer | None = None, **changes: Any
) -> DoctorContext:
    async def jdk(configured: str) -> Jdk | None:
        return JDK

    ctx = DoctorContext(
        config=ON,
        registry=ConnectorRegistry([]),
        run=no_xcode,
        env={},
        android_sdk=lambda configured: sdk,
        avds=lambda found: avds(found, {}, root),
        adb=(lambda: server.adb()) if server is not None else (lambda: Adb(str(root / "no-adb.sock"))),
        scrcpy=fetched_jars(root / "scrcpy", fetched=False),
        jdk=jdk,
    )
    return dataclasses.replace(ctx, **changes)


async def test_while_android_is_off_nothing_is_checked(tmp_path: Path) -> None:
    ctx = context(tmp_path, sdk=None, config=SimConfig.defaults().with_values(android=False))
    assert (await check_android_sdk(ctx)).detail.startswith("off; `sim-mirror config set android.enabled true`")
    for check, name in (
        (check_android_devices, "android devices"),
        (check_scrcpy, "scrcpy"),
        (check_gradle, "android builds"),
    ):
        assert await check(ctx) == CheckResult(name, "ok", "off with Android devices")


async def test_without_an_sdk_the_others_are_skipped(tmp_path: Path) -> None:
    ctx = context(tmp_path, sdk=None)
    found = await check_android_sdk(ctx)
    assert (
        found.status == "ok"
        and found.detail.startswith("no Android SDK on this Mac")
        and "android.sdk_path" in found.fix
    )
    for check in (check_android_devices, check_scrcpy, check_gradle):
        assert (await check(ctx)).status == "skip"


async def test_the_sdk_its_server_and_its_avds_are_said_and_a_broken_avd_warns(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    system = image(sdk, "36")
    avd(tmp_path / ".android/avd", "Pixel_9", f"avd.ini.displayname=Pixel 9\nimage.sysdir.1={system}\n")
    async with FakeAdbServer() as server:
        found = await check_android_sdk(context(tmp_path, sdk=sdk, server=server))
    assert found == CheckResult(
        "android sdk",
        "ok",
        f"adb 37.0.0, emulator 36.4.9 at {sdk.root}; adb's server answers (protocol 41); AVDs: Pixel 9",
    )
    avd(tmp_path / ".android/avd", "Broken", "avd.ini.displayname=Broken\nimage.sysdir.1=system-images/x/\n")
    found = await check_android_sdk(context(tmp_path, sdk=sdk))
    assert found.status == "warn" and "adb's server is not running; it starts with the first Android device" in (
        found.detail
    )
    assert "Broken (its system image is not installed" in found.detail and "Device Manager" in found.fix
    bare = sdk_at(tmp_path / "bare", adb=False, emulator=False)
    found = await check_android_sdk(context(tmp_path / "nobody", sdk=bare))
    assert found.status == "warn" and found.detail.startswith("adb missing, emulator missing at")
    assert found.detail.endswith("no AVDs yet: make one in Android Studio's Device Manager")


async def test_devices_are_listed_and_a_phone_still_to_be_allowed_warns(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    assert await check_android_devices(context(tmp_path, sdk=sdk)) == CheckResult(
        "android devices", "ok", "none running: adb's server is not running"
    )
    async with FakeAdbServer() as server:
        ctx = context(tmp_path, sdk=sdk, server=server)
        assert (await check_android_devices(ctx)).detail == "none running or connected"
        server.emulator().device("R58OFF", "unauthorized")
        found = await check_android_devices(ctx)
        assert found.status == "warn" and found.detail == (
            "emulator-5554 (emulator, device); R58OFF (usb, unauthorized)"
        )
        assert found.fix.startswith("Unlock the phone")
        phones_off = dataclasses.replace(ctx, config=ON.with_values(android_phones=False))
        assert await check_android_devices(phones_off) == CheckResult(
            "android devices", "ok", "emulator-5554 (emulator, device); phones are off (android.phones)"
        )


async def test_scrcpy_says_whether_it_is_on_and_set_up(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    off = context(tmp_path, sdk=sdk, config=ON.with_values(scrcpy=False))
    assert (await check_scrcpy(off)).detail.startswith("off (android.scrcpy.enabled)")
    found = await check_scrcpy(context(tmp_path, sdk=sdk))
    assert found.status == "warn" and found.detail.startswith("scrcpy's server is not set up")
    jars = fetched_jars(tmp_path / "fetched")
    assert await check_scrcpy(context(tmp_path, sdk=sdk, scrcpy=jars)) == CheckResult(
        "scrcpy", "ok", f"release 9.9.9 at {tmp_path / 'fetched' / jars.release.file_name}, checked by its SHA-256"
    )


async def test_gradle_says_which_jdk_builds_while_build_tools_are_on(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    off = context(tmp_path, sdk=sdk, config=ON.with_values(build_tools=False))
    assert await check_gradle(off) == CheckResult("android builds", "ok", "off with build tools (build.tools)")
    assert await check_gradle(context(tmp_path, sdk=sdk)) == CheckResult(
        "android builds", "ok", "JDK of unknown version at /opt/jdk (android.java_home)"
    )

    async def none(configured: str) -> Jdk | None:
        return None

    found = await check_gradle(context(tmp_path, sdk=sdk, jdk=none))
    assert found.status == "warn" and "android.java_home" in found.fix


async def test_a_mac_with_android_but_no_xcode_warns_of_xcode_and_says_which_connector_android_uses(
    tmp_path: Path,
) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    android = FakeConnector("android", kinds=frozenset({"android"}))
    ctx = context(tmp_path, sdk=sdk, registry=ConnectorRegistry([android]))
    assert await check_xcode(ctx) == CheckResult(
        "xcode", "warn", "no Xcode is selected; iOS simulators need Xcode, Android devices do not", INSTALL_XCODE
    )
    found = await check_connectors(ctx)
    assert found.status == "warn" and found.detail.endswith("; Android devices use android")
    # Without the SDK, a missing Xcode fails as it always has.
    assert (await check_xcode(context(tmp_path, sdk=None))).status == "fail"
    # A simulator's connector, and none for Android: what Android lacks is said beside it.
    found = await check_connectors(context(tmp_path, sdk=sdk, registry=ConnectorRegistry([FakeConnector("idb")])))
    assert found.status == "ok" and found.detail.startswith("idb is used (")
    assert "; no connector can show an Android device: " in found.detail
    assert (await check_connectors(context(tmp_path, sdk=sdk))).status == "fail"
