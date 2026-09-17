# SPDX-License-Identifier: Apache-2.0
"""Every built-in connector keeps the connector contract, and the contract names each promise a connector breaks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorReport, DeviceSession
from sim_mirror.connectors.idb.connector import IdbConnector
from sim_mirror.connectors.native.connector import NativeConnector
from sim_mirror.connectors.native.helper import HelperLauncher, HelperVersion
from sim_mirror.connectors.simctl.connector import SimctlConnector
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing import contract
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
from sim_mirror.testing.native import FakeHelperSpawn, short_run_dir

CONFIG = SimConfig.defaults()


async def test_the_idb_connector_keeps_the_contract() -> None:
    connector = IdbConnector(FakeLauncher(), find=lambda configured: "/bin/idb_companion", choose=FakeXcodeSelect())
    assert await check_connector(connector, CONFIG, BOOTED_UDID) == []


async def test_the_native_connector_keeps_the_contract(tmp_path: Path) -> None:
    binary = tmp_path / "sim-mirror-helper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    spawn = FakeHelperSpawn()

    async def version(path: str) -> HelperVersion:
        return HelperVersion(__version__, 1, "1171.7")

    with short_run_dir() as run:
        launcher = HelperLauncher(
            run_dir=run, log_dir=tmp_path, owner_tag="SimMirrorTest", spawn=spawn, signal_group=spawn.signal_group
        )
        connector = NativeConnector(
            launcher, candidates=lambda: [binary], ask_version=version, choose=FakeXcodeSelect()
        )
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


async def test_a_stream_that_does_not_start_at_a_key_frame_and_a_tap_that_fails_are_named() -> None:
    class Refusing(FakeEngine):
        async def hid(self, events: Any) -> None:
            raise ConnectorError("input cannot reach the simulator")

    engine = Refusing()
    engine.chunks = [b"\x00\x00\x00\x01\x41delta"]
    assert await check_connector(FakeConnector(engine=engine), CONFIG, BOOTED_UDID) == [
        "an H.264 stream starts at a key frame carrying its sequence parameter set",
        "a tap in the middle of the screen is taken (input cannot reach the simulator)",
    ]


async def test_a_stream_that_sends_nothing_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract, "STREAM_WAIT_S", 0.01)
    engine = FakeEngine()
    engine.chunks = []
    assert await check_connector(FakeConnector(engine=engine), CONFIG, BOOTED_UDID) == [
        "an H.264 stream sends its first chunk within 0.01 seconds"
    ]

    class Plain:
        """A stream with nothing to close, as a connector may return."""

        def __aiter__(self) -> Plain:
            return self

        async def __anext__(self) -> bytes:
            raise StopAsyncIteration

    plain = FakeEngine()
    plain.h264 = lambda **settings: Plain()  # type: ignore[assignment,method-assign]
    assert await check_connector(FakeConnector(engine=plain), CONFIG, BOOTED_UDID) == [
        "an H.264 stream sends its first chunk within 0.01 seconds"
    ]
