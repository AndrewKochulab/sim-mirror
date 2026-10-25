# SPDX-License-Identifier: Apache-2.0
"""The android connector and Gradle on a real, running Android emulator: never run by CI or `make test`, only by
`make live-android`.

    SIM_MIRROR_LIVE_ANDROID_UDID=avd-<name>-<hex> make live-android

It runs the connector contract against the device through adb alone -- which taps the middle of its screen -- then,
with `SIM_MIRROR_LIVE_SCRCPY` naming scrcpy's server 5.0.1, through scrcpy, streaming and reading the screen as a
viewer and an agent do. With `SIM_MIRROR_LIVE_GRADLE=1` it builds `examples/android-app` and runs its instrumented
tests on the device. `SIM_MIRROR_LIVE_ANDROID_SDK` names the SDK (default: Android Studio's). Nothing here starts or
stops the emulator: start it first, from Android Studio or `sim-mirror devices choose`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sim_mirror.build.gradle import GradleToolchain
from sim_mirror.build.runner import Runner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.android.connector import AndroidConnector
from sim_mirror.connectors.base import Capability
from sim_mirror.core.android_devices import AndroidServices
from sim_mirror.platform.adb import DEFAULT_PORT, Adb
from sim_mirror.testing.contract import check_connector
from sim_mirror.testing.fakes import MemoryStateStore
from sim_mirror.testing.rig import scope

UDID = os.environ.get("SIM_MIRROR_LIVE_ANDROID_UDID", "")
SDK = os.environ.get("SIM_MIRROR_LIVE_ANDROID_SDK", str(Path.home() / "Library/Android/sdk"))
SCRCPY = os.environ.get("SIM_MIRROR_LIVE_SCRCPY", "")
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "android-app"

pytestmark = [
    pytest.mark.live_android,
    pytest.mark.allow_subprocess,
    pytest.mark.skipif(not UDID, reason="SIM_MIRROR_LIVE_ANDROID_UDID names no running Android emulator"),
]


def services(root: Path) -> AndroidServices:
    """The services a daemon builds, on the Mac's own adb server -- the test guard points SimMirror at none."""
    made = AndroidServices.build(MemoryStateStore(root), adb=Adb(f"127.0.0.1:{DEFAULT_PORT}"))
    made.sdk_path = SDK
    return made


def config(*, scrcpy: bool) -> SimConfig:
    return SimConfig.defaults().with_values(
        android=True, android_sdk_path=SDK, scrcpy=scrcpy, scrcpy_path=SCRCPY if scrcpy else ""
    )


async def test_the_android_connector_keeps_the_contract_through_adb(tmp_path: Path) -> None:
    assert await check_connector(AndroidConnector(services(tmp_path)), config(scrcpy=False), UDID) == []


@pytest.mark.skipif(not SCRCPY, reason="SIM_MIRROR_LIVE_SCRCPY names no scrcpy server")
async def test_through_scrcpy_the_screen_is_live_and_touched(tmp_path: Path) -> None:
    connector = AndroidConnector(services(tmp_path))
    settings = config(scrcpy=True)
    assert await check_connector(connector, settings, UDID) == []
    session = await connector.attach(UDID, settings)
    try:
        assert Capability.STREAM_H264 in session.capabilities
        stream = session.screen.h264(fps=30, scale=0.5, key_frame_s=1.0, bitrate=2_000_000)
        try:
            first = await anext(stream)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
        assert first.startswith(b"\x00\x00\x00\x01")
        assert session.reader is not None
        assert (await session.reader.accessibility())["elements"]
    finally:
        await session.close()


@pytest.mark.skipif(os.environ.get("SIM_MIRROR_LIVE_GRADLE") != "1", reason="SIM_MIRROR_LIVE_GRADLE is not 1")
async def test_gradle_builds_and_tests_the_example_on_the_device(tmp_path: Path) -> None:
    android = services(tmp_path)
    runner = Runner(MemoryStateStore(tmp_path), {"android": GradleToolchain(android.directory.serial)})
    owner = scope("live-android")
    for kind, verdict in (("build", "build ok"), ("test", "test ok")):
        build = await runner.start(
            owner, kind=kind, folder=EXAMPLE, udid=UDID, developer_dir=SDK, timeout_s=900, device_kind="android"
        )
        said = await runner.result(owner.id, build.id, 900)
        assert said.startswith(verdict), said
    assert build.state == "succeeded"
