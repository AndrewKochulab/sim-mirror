# SPDX-License-Identifier: Apache-2.0
"""The runner every toolchain shares: which toolchain builds for which kind of device."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.build.runner import BuildRefused
from sim_mirror.build.xcodebuild import BuildRunner, XcodeToolchain
from sim_mirror.testing.fakes import MemoryStateStore
from sim_mirror.testing.rig import scope


def test_xcodebuild_builds_for_simulators_and_iphones_and_another_kind_is_refused(tmp_path: Path) -> None:
    runner = BuildRunner(MemoryStateStore(tmp_path))
    assert isinstance(runner.toolchain("simulator"), XcodeToolchain)
    assert runner.toolchain("physical") is runner.toolchain("simulator")
    with pytest.raises(BuildRefused, match="nothing here builds for this android device"):
        runner.toolchain("android")


async def test_a_run_for_a_kind_nothing_builds_is_refused_before_anything_starts(tmp_path: Path) -> None:
    runner = BuildRunner(MemoryStateStore(tmp_path))
    with pytest.raises(BuildRefused, match="nothing here builds"):
        await runner.start(
            scope("topic-1", "ws"),
            kind="build",
            folder=tmp_path,
            udid="avd-Pixel-9-3fa2c1",
            developer_dir="",
            timeout_s=60,
            device_kind="android",
        )
    assert runner.runs() == []
