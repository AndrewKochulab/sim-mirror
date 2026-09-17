# SPDX-License-Identifier: Apache-2.0
"""Choosing a connector: auto takes the first that can be used and says why a lesser one; a named one or nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.idb.connector import IdbConnector
from sim_mirror.connectors.mcpbridge.connector import McpBridgeConnector
from sim_mirror.connectors.native.connector import NativeConnector
from sim_mirror.connectors.registry import ENTRY_POINT_GROUP, ConnectorContext, ConnectorRegistry
from sim_mirror.connectors.simctl.connector import SimctlConnector
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import FakeConnector, FakeXcrun, MemoryStateStore

CONFIG = SimConfig.defaults()


def registry(*connectors: FakeConnector) -> ConnectorRegistry:
    return ConnectorRegistry(connectors, copy=HostCopy(doctor_hint="Ask the doctor."))


async def test_auto_takes_native_first_and_keeps_the_others_that_can_be_used_as_candidates() -> None:
    native, idb, simctl = FakeConnector("native"), FakeConnector("idb"), FakeConnector("simctl")
    chosen = await registry(simctl, idb, native).select(CONFIG)
    assert chosen.connector is native and chosen.report is not None and chosen.report.available
    assert chosen.fallback_reason is None and chosen.refusal is None
    assert [connector for connector, _report in chosen.candidates] == [idb, simctl]
    assert [connector.name for connector, _report in chosen.choices] == ["native", "idb", "simctl"]


async def test_auto_takes_idb_when_native_cannot_be_used_and_a_later_refusal_is_no_fallback_reason() -> None:
    native = FakeConnector("native", available=False, reasons=("the helper is not built.",))
    idb, simctl = FakeConnector("idb"), FakeConnector("simctl", available=False, reasons=("no xcrun.",))
    chosen = await registry(native, idb, simctl).select(CONFIG)
    assert chosen.connector is idb and chosen.candidates == ()
    assert chosen.fallback_reason == "the helper is not built."


async def test_auto_falls_back_to_simctl_and_says_why() -> None:
    idb = FakeConnector("idb", available=False, reasons=("idb_companion is not installed.",))
    simctl = FakeConnector("simctl")
    chosen = await registry(idb, simctl).select(CONFIG)
    assert chosen.connector is simctl and chosen.fallback_reason == "idb_companion is not installed."
    assert (await registry().select(CONFIG)).choices == ()


async def test_auto_with_nothing_usable_refuses_with_every_reason() -> None:
    idb = FakeConnector("idb", available=False, reasons=("no companion.",))
    simctl = FakeConnector("simctl", available=False, reasons=("no xcrun.",))
    chosen = await registry(idb, simctl, FakeConnector("other")).select(CONFIG)
    assert chosen.connector is None and chosen.report is not None and chosen.report.name == "simctl"
    assert chosen.refusal == "No connector can reach a simulator here. no companion. no xcrun. Ask the doctor."
    empty = await registry().select(CONFIG)
    assert empty.refusal == "No connector can reach a simulator here. No connector is installed. Ask the doctor."
    assert empty.report is None


async def test_a_named_connector_is_used_or_refused_never_replaced() -> None:
    idb = FakeConnector("idb", available=False, reasons=("no companion.",))
    simctl = FakeConnector("simctl")
    found = registry(idb, simctl)
    assert (await found.select(CONFIG.with_values(connector="simctl"))).connector is simctl
    refused = await found.select(CONFIG.with_values(connector="idb"))
    assert (
        refused.connector is None
        and refused.refusal == "The idb connector cannot be used here. no companion. Ask the doctor."
    )
    unknown = await found.select(CONFIG.with_values(connector="swift"))
    assert unknown.refusal == "There is no connector named swift; installed: idb, simctl."
    assert (await registry().select(CONFIG.with_values(connector="idb"))).refusal.endswith("installed: none.")


async def test_every_connector_reports_for_the_doctor() -> None:
    reports = await registry(FakeConnector("idb"), FakeConnector("simctl", available=False)).reports(CONFIG)
    assert [(report.name, report.available) for report in reports] == [("idb", True), ("simctl", False)]


class _Entry:
    def __init__(self, name: str, factory: Any = None, error: Exception | None = None) -> None:
        self.name, self._factory, self._error = name, factory, error

    def load(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._factory


def test_discovery_keeps_the_built_ins_adds_installed_connectors_and_skips_what_does_not_load(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seen: dict[str, Any] = {}

    def entry_points(**selection: Any) -> list[_Entry]:
        seen.update(selection)
        return [
            _Entry("swift", factory=lambda context: FakeConnector("swift")),
            _Entry("idb", factory=lambda context: FakeConnector("idb")),
            _Entry("broken", error=ImportError("no module named swift_helper")),
        ]

    context = ConnectorContext(
        state=MemoryStateStore(tmp_path), copy=HostCopy(), simctl_for=lambda developer_dir: Simctl(FakeXcrun())
    )
    found = ConnectorRegistry.discover(context, entry_points=entry_points)
    assert seen == {"group": ENTRY_POINT_GROUP}
    assert found.names() == ["native", "idb", "simctl", "mcpbridge", "swift"]
    assert isinstance(found.get("idb"), IdbConnector) and isinstance(found.get("simctl"), SimctlConnector)
    assert isinstance(found.get("native"), NativeConnector)
    assert isinstance(found.get("mcpbridge"), McpBridgeConnector)
    assert found.get("missing") is None
    assert "is built in" in caplog.text and "broken could not be loaded" in caplog.text


def test_discovery_reads_this_environments_installed_packages(tmp_path: Path) -> None:
    context = ConnectorContext(state=MemoryStateStore(tmp_path), copy=HostCopy(), simctl_for=lambda d: Simctl())
    assert ConnectorRegistry.discover(context).names()[:4] == ["native", "idb", "simctl", "mcpbridge"]
