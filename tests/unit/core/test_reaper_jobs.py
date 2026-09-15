# SPDX-License-Identifier: Apache-2.0
"""The reaper's jobs: run after each reap, each on its own, so one that fails is logged and the rest still run."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from sim_mirror.core.reaper import Reaper
from sim_mirror.testing.rig import DeviceRig


async def test_the_reaper_runs_its_jobs_after_each_reap_and_logs_one_that_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)
    ran: list[str] = []
    reaper = Reaper(rig.manager, every_s=30, sleep=lambda seconds: asyncio.sleep(0))

    async def broken() -> None:
        raise RuntimeError("boom")

    async def job() -> None:
        ran.append("job")

    reaper.add(broken)
    reaper.add(job)
    with caplog.at_level(logging.ERROR):
        reaper.start()
        for _ in range(500):
            if len(ran) >= 2:
                break
            await asyncio.sleep(0.001)
        await reaper.stop()
    assert len(ran) >= 2 and "a reaper job failed" in caplog.text
