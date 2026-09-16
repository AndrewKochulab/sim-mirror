# SPDX-License-Identifier: Apache-2.0
"""Every built-in connector keeps the connector contract, and the contract names each promise a connector breaks."""

from __future__ import annotations

from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorReport, DeviceSession
from sim_mirror.connectors.idb.connector import IdbConnector
from sim_mirror.connectors.simctl.connector import SimctlConnector
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.contract import check_connector
from sim_mirror.testing.fakes import (
    BOOTED_UDID,
    FakeConnector,
    FakeEngine,
    FakeLauncher,
    FakeXcodeSelect,
    FakeXcrun,
    tiny_jpeg,
)

CONFIG = SimConfig.defaults()


async def test_the_idb_connector_keeps_the_contract() -> None:
    connector = IdbConnector(FakeLauncher(), find=lambda configured: "/bin/idb_companion", choose=FakeXcodeSelect())
    assert await check_connector(connector, CONFIG, BOOTED_UDID) == []


async def test_the_simctl_connector_keeps_the_contract() -> None:
    fake = FakeXcrun().with_screenshot(tiny_jpeg(1206, 2622))
    connector = SimctlConnector(lambda developer_dir: Simctl(fake), has_xcrun=lambda: True)
    assert await check_connector(connector, CONFIG, BOOTED_UDID) == []


async def test_a_connector_that_cannot_be_used_keeps_it_by_saying_why() -> None:
    assert await check_connector(FakeConnector(available=False), CONFIG, BOOTED_UDID) == []
    view_only = FakeConnector(capabilities=frozenset({Capability.SCREENSHOT}))
    assert await check_connector(view_only, CONFIG, BOOTED_UDID) == []
    lifecycle_only = FakeConnector(capabilities=frozenset({Capability.LIFECYCLE}))
    assert await check_connector(lifecycle_only, CONFIG, BOOTED_UDID) == []


class Broken(FakeConnector):
    """A connector that breaks every promise it can."""

    async def probe(self, config: SimConfig) -> ConnectorReport:
        report = await super().probe(config)
        capabilities: Any = frozenset({*report.capabilities, "not-a-capability"})
        return ConnectorReport("other", True, capabilities, reasons=("why not",))

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        session = await super().attach(udid, config)
        return DeviceSession(
            connector="renamed",
            capabilities=frozenset({Capability.INPUT_TOUCH, Capability.ELEMENT_TREE, Capability.SCREENSHOT}),
            screen=session.screen,
            is_alive=lambda: False,
        )

    async def reap_orphans(self) -> int:
        return -1


class Shapeless(FakeEngine):
    async def accessibility(self) -> Any:
        return ["not", "an", "object"]


async def test_a_broken_connector_is_told_every_promise_it_broke() -> None:
    engine = FakeEngine()
    engine.screen = type(engine.screen)(0, 0, 0, 0, 0.0)
    engine.shot = type(engine.shot)(b"PNG", 0, 0)
    problems = await check_connector(Broken(engine=engine, available=True), CONFIG, BOOTED_UDID)
    assert problems == [
        "the report names 'other', not the connector's name 'fake'",
        "the report's capabilities are Capability members",
        "a connector that can be used gives no reasons",
        "the session names 'renamed', not 'fake'",
        "the session has the capabilities the report promised",
        "a connector with input capabilities gives an InputSink",
        "a connector with element_tree gives a ScreenReader",
        "a session just attached is alive",
        "the screen has a size in pixels and in points, and a scale",
        "a screenshot is a JPEG with a size",
        "reap_orphans answers how many it ended",
    ]


async def test_a_reader_that_answers_no_object_and_a_session_that_outlives_closing_are_named() -> None:
    class Undying(FakeConnector):
        async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
            session = await super().attach(udid, config)
            session.__class__ = Immortal
            return session

    class Immortal(DeviceSession):
        @property
        def alive(self) -> bool:
            return True

    problems = await check_connector(Undying(engine=Shapeless()), CONFIG, BOOTED_UDID)
    assert problems == ["the accessibility document is an object", "a closed session is not alive"]
    unexplained = FakeConnector(available=False)
    unexplained.reasons = ()
    assert await check_connector(unexplained, CONFIG, BOOTED_UDID) == [
        "a connector that cannot be used says why, in its report's reasons"
    ]
