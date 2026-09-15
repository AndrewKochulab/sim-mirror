# SPDX-License-Identifier: Apache-2.0
"""The idb connector: usable wherever idb_companion is, full control, and a session whose close ends its companion."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorUnavailable
from sim_mirror.connectors.idb.companion import Companion
from sim_mirror.connectors.idb.connector import CAPABILITIES, IdbConnector, create
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, FakeLauncher, MemoryStateStore

CONFIG = SimConfig.defaults()


async def test_without_a_companion_it_cannot_be_used_and_says_what_to_install() -> None:
    connector = IdbConnector(FakeLauncher(), find=lambda configured: None)
    report = await connector.probe(CONFIG)
    assert not report.available and "brew install facebook/fb/idb-companion" in report.reasons[0]
    with pytest.raises(ConnectorUnavailable) as refused:
        await connector.attach(BOOTED_UDID, CONFIG.with_values(companion_path="/x/idb_companion"))
    assert refused.value.status == 409 and "/x/idb_companion cannot be run" in str(refused.value)


async def test_with_a_companion_it_has_every_capability_but_building() -> None:
    connector = IdbConnector(FakeLauncher(), find=lambda configured: "/opt/homebrew/bin/idb_companion")
    report = await connector.probe(CONFIG)
    assert report.available and report.capabilities == CAPABILITIES and report.name == "idb"
    assert Capability.BUILD_PREVIEW not in CAPABILITIES and Capability.ELEMENT_TREE in CAPABILITIES
    assert report.versions == {"idb_companion": "/opt/homebrew/bin/idb_companion"}


async def test_attaching_starts_a_companion_and_closing_the_session_ends_it() -> None:
    launcher = FakeLauncher()
    connector = IdbConnector(launcher, copy=HostCopy(), find=lambda configured: "/bin/idb_companion")
    session = await connector.attach(BOOTED_UDID, CONFIG)
    assert launcher.started == [("/bin/idb_companion", BOOTED_UDID)]
    assert session.screen is launcher.engine and session.input is launcher.engine and session.reader is launcher.engine
    assert session.alive
    await session.close()
    assert launcher.stopped == [BOOTED_UDID] and not session.alive
    assert await connector.reap_orphans() == 2 and launcher.reaped == 1


async def test_a_session_is_not_alive_once_its_companion_exits() -> None:
    launcher = FakeLauncher()
    started = []
    real_start = launcher.start

    async def start(binary: str, udid: str) -> Companion:
        companion = await real_start(binary, udid)
        started.append(companion)
        return companion

    launcher.start = start  # type: ignore[method-assign]
    session = await IdbConnector(launcher, find=lambda configured: "/bin/c").attach(BOOTED_UDID, CONFIG)
    assert session.alive
    started[0].process.returncode = 1
    assert not session.alive


def test_a_host_gets_one_whose_companions_live_in_its_folders(tmp_path: Path) -> None:
    state = MemoryStateStore(tmp_path)
    connector = create(ConnectorContext(state=state, copy=HostCopy(), simctl_for=lambda d: Simctl()))
    assert connector.name == "idb" and connector._launcher._run_dir == state.run_dir()
    assert connector._launcher._tag == state.owner_tag
