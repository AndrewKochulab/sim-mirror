# SPDX-License-Identifier: Apache-2.0
"""The reaping loop keeps going through failures and stops when told; a status names the connector a viewer gets."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorReport
from sim_mirror.connectors.registry import Selection
from sim_mirror.core.availability import Verdict
from sim_mirror.core.reaper import Reaper
from sim_mirror.core.status import scope_status
from sim_mirror.testing.fakes import FakeConnector
from sim_mirror.testing.rig import DeviceRig


async def test_the_loop_reaps_on_its_schedule_survives_a_failure_and_stops(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)
    calls: list[int] = []
    waits: list[float] = []

    async def reap() -> list[str]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        await asyncio.sleep(0)

    rig.manager.reap = reap  # type: ignore[method-assign]
    reaper = Reaper(rig.manager, every_s=30, sleep=sleep)
    assert not reaper.running
    reaper.start()
    reaper.start()
    while len(calls) < 3:
        await asyncio.sleep(0)
    assert reaper.running and waits[:3] == [30, 30, 30] and "reaping simulators failed" in caplog.text
    await reaper.stop()
    await reaper.stop()
    assert not reaper.running


def test_a_status_before_any_device_says_what_the_connector_chosen_can_do() -> None:
    config = SimConfig.defaults()
    idb = FakeConnector("idb")
    report = ConnectorReport("simctl", True, frozenset())
    chosen = scope_status(Verdict(config, None, Selection(idb, report, fallback_reason="no idb")), None, 0.0)
    assert chosen["connector"] == "simctl" and chosen["capabilities"] == [] and chosen["fallback_reason"] == "no idb"
    refused = scope_status(Verdict(config, "off", Selection(None, report, refusal="off")), None, 0.0)
    assert refused["connector"] is None and refused["capabilities"] == [] and refused["reason"] == "off"
    bare = scope_status(Verdict(config, "not a Mac"), None, 0.0)
    assert bare["connector"] is None and bare["fallback_reason"] is None
    assert Verdict(config, None).connector is None
