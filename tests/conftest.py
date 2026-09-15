# SPDX-License-Identifier: Apache-2.0
"""What every test runs under: no real Simulator, Xcode or agent, and SimMirror's folders in a temporary one."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.testing import guards


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    guards.isolate_state(monkeypatch, tmp_path / "sim-mirror")


@pytest.fixture(autouse=True)
def _no_real_programs(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("allow_subprocess"):
        return
    guards.install_subprocess_guard(monkeypatch)
