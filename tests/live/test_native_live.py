# SPDX-License-Identifier: Apache-2.0
"""The native connector on a real, booted simulator: never run by CI or `make test`, only by `make live`.

    SIM_MIRROR_LIVE_UDID=<booted UDID> make live

It runs the connector contract against the device -- which taps the middle of its screen -- then streams and reads it
the way a viewer and an agent do. `SIM_MIRROR_LIVE_HELPER` names the helper (default: the one `make helper-build` or
`swift build` left in helper/.build), and `SIM_MIRROR_LIVE_DEVELOPER_DIR` the Xcode (default: the one xcode-select
names).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.native.connector import NativeConnector
from sim_mirror.connectors.native.helper import HelperLauncher
from sim_mirror.testing.contract import check_connector
from sim_mirror.testing.native import short_run_dir

UDID = os.environ.get("SIM_MIRROR_LIVE_UDID", "")
HELPER_BUILDS = Path(__file__).resolve().parents[2] / "helper" / ".build"
BUILT = (HELPER_BUILDS / "universal", HELPER_BUILDS / "release", HELPER_BUILDS / "debug")

pytestmark = [
    pytest.mark.live,
    pytest.mark.allow_subprocess,
    pytest.mark.skipif(not UDID, reason="SIM_MIRROR_LIVE_UDID names no booted simulator"),
]


def helper() -> str:
    named = os.environ.get("SIM_MIRROR_LIVE_HELPER", "")
    found = [folder / "sim-mirror-helper" for folder in BUILT if (folder / "sim-mirror-helper").is_file()]
    if not named and not found:
        pytest.skip("no native helper: run make helper-build, or name one in SIM_MIRROR_LIVE_HELPER")
    return named or str(found[0])


@pytest.fixture
def config() -> SimConfig:
    return SimConfig.defaults().with_values(
        connector="native",
        native_helper_path=helper(),
        developer_dir=os.environ.get("SIM_MIRROR_LIVE_DEVELOPER_DIR", ""),
    )


@pytest.fixture
def connector(tmp_path: Path) -> Iterator[NativeConnector]:
    with short_run_dir() as run_dir:
        yield NativeConnector(HelperLauncher(run_dir=run_dir, log_dir=tmp_path, owner_tag="live"))


async def test_the_native_connector_keeps_the_contract_on_a_real_device(
    connector: NativeConnector, config: SimConfig
) -> None:
    assert await check_connector(connector, config, UDID) == []


async def test_a_real_device_streams_and_reads(connector: NativeConnector, config: SimConfig) -> None:
    report = await connector.probe(config)
    assert report.available, report.reasons
    assert Capability.STREAM_H264 in report.capabilities
    session = await connector.attach(UDID, config)
    try:
        screen = await session.screen.describe()
        stream = session.screen.h264(fps=30, scale=0.5, key_frame_s=1.0, bitrate=2_000_000)
        try:
            first = await anext(stream)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
        assert first.startswith(b"\x00\x00\x00\x01")
        wide = await session.screen.screenshot(max_width=900, quality=70)
        assert wide.width == min(900, screen.width_px)
        assert session.reader is not None
        assert isinstance(await session.reader.accessibility(), dict)
    finally:
        await session.close()
