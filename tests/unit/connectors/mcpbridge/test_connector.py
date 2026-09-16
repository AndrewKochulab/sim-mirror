# SPDX-License-Identifier: Apache-2.0
"""The mcpbridge connector: the screen as simctl shows it, read through Xcode 27, never touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorUnavailable
from sim_mirror.connectors.mcpbridge.client import BridgeClient
from sim_mirror.connectors.mcpbridge.connector import CAPABILITIES, NAME, McpBridgeConnector, create
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl import connector as simctl
from sim_mirror.connectors.simctl.capture import SimctlScreen
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, FakeBridge, FakeXcrun, MemoryStateStore

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
CONFIG = SimConfig.defaults().with_values(connector=NAME, developer_dir=XCODE_27)


def connector_on(
    xcrun: FakeXcrun, bridge: FakeBridge | None = None, root: Path = Path("/nonexistent")
) -> McpBridgeConnector:
    context = ConnectorContext(
        state=MemoryStateStore(root), copy=HostCopy(), simctl_for=lambda d: Simctl(xcrun, developer_dir=d), xcrun=xcrun
    )
    made = create(context)
    if bridge is not None:
        made._reader_for = lambda udid, developer_dir: BridgeReader(
            udid, developer_dir, client=BridgeClient(developer_dir, spawn=bridge.spawn), find=lambda d: _found()
        )
    return made


async def _found() -> str:
    return "/x/mcpbridge"


async def test_it_can_do_what_simctl_can_and_read_the_screen_but_not_touch_it() -> None:
    assert simctl.CAPABILITIES | {Capability.ELEMENT_TREE} == CAPABILITIES
    assert not any(capability.value.startswith("input_") for capability in CAPABILITIES)
    report = await connector_on(FakeXcrun().with_xcode("27.0")).probe(CONFIG)
    assert report.available and report.name == NAME and report.capabilities == CAPABILITIES
    assert report.versions == {"mcpbridge": f"{XCODE_27}/usr/bin/mcpbridge"}


async def test_without_xcode_27_it_cannot_be_used_and_says_so_with_where_to_change_it() -> None:
    connector = connector_on(FakeXcrun().with_xcode("26.6", "17F42"))
    report = await connector.probe(CONFIG)
    assert not report.available and report.reasons == (
        f"Reading the screen through Xcode needs Xcode 27 or later; the Xcode in use at {XCODE_27} is older, or has no "
        "mcpbridge (`sim-mirror config`).",
    )
    with pytest.raises(ConnectorUnavailable) as refused:
        await connector.attach(BOOTED_UDID, CONFIG)
    assert refused.value.status == 409 and str(refused.value) == report.reasons[0]


async def test_attaching_shows_the_screen_through_simctl_reads_it_through_xcode_and_closing_ends_the_session(
    tmp_path: Path,
) -> None:
    xcrun = FakeXcrun().with_xcode("27.0")
    bridge = FakeBridge(folder=tmp_path)
    connector = connector_on(xcrun, bridge)
    session = await connector.attach(BOOTED_UDID, CONFIG)
    assert session.connector == NAME and session.capabilities == CAPABILITIES and session.input is None
    assert isinstance(session.screen, SimctlScreen) and session.fps_limit == simctl.FPS_LIMIT
    assert session.reader is not None
    assert (await session.reader.accessibility())["elements"]
    await session.close()
    assert bridge.tools()[-1] == "DeviceInteractionEndSession"
    assert await connector.reap_orphans() == 0 and connector.name == NAME


async def test_a_connector_made_without_a_reader_makes_its_own() -> None:
    connector = McpBridgeConnector(lambda d: Simctl(FakeXcrun(), developer_dir=d), find=lambda d: _found())
    session = await connector.attach(BOOTED_UDID, CONFIG)
    assert isinstance(session.reader, BridgeReader) and session.reader.developer_dir == XCODE_27
    await session.close()
