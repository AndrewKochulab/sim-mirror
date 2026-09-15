# SPDX-License-Identifier: Apache-2.0
"""The simctl connector: needs only xcrun, shows the screen a few frames a second, and cannot touch it."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorUnavailable
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl.capture import SimctlScreen
from sim_mirror.connectors.simctl.connector import CAPABILITIES, FPS_LIMIT, SimctlConnector, create
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, FakeXcrun, MemoryStateStore

CONFIG = SimConfig.defaults()


async def test_without_xcrun_it_cannot_be_used() -> None:
    connector = SimctlConnector(lambda developer_dir: Simctl(FakeXcrun()), has_xcrun=lambda: False)
    report = await connector.probe(CONFIG)
    assert not report.available and "no xcrun" in report.reasons[0]
    with pytest.raises(ConnectorUnavailable, match="no xcrun") as refused:
        await connector.attach(BOOTED_UDID, CONFIG)
    assert refused.value.status == 409


async def test_it_can_show_and_manage_a_device_but_not_touch_or_read_it() -> None:
    made: list[str] = []

    def simctl_for(developer_dir: str) -> Simctl:
        made.append(developer_dir)
        return Simctl(FakeXcrun(), developer_dir=developer_dir)

    connector = SimctlConnector(simctl_for, has_xcrun=lambda: True)
    report = await connector.probe(CONFIG)
    assert report.available and report.capabilities == CAPABILITIES
    assert not CAPABILITIES & {Capability.INPUT_TOUCH, Capability.ELEMENT_TREE, Capability.STREAM_H264}
    session = await connector.attach(BOOTED_UDID, CONFIG.with_values(developer_dir="/X.app/Contents/Developer"))
    assert isinstance(session.screen, SimctlScreen) and session.input is None and session.reader is None
    assert session.fps_limit == FPS_LIMIT and made == ["/X.app/Contents/Developer"]
    assert await connector.reap_orphans() == 0


async def test_a_host_gets_one_over_its_simctl(tmp_path: Path) -> None:
    connector = create(
        ConnectorContext(state=MemoryStateStore(tmp_path), copy=HostCopy(), simctl_for=lambda d: Simctl(FakeXcrun()))
    )
    assert connector.name == "simctl" and isinstance(await connector.probe(CONFIG), type(await connector.probe(CONFIG)))
