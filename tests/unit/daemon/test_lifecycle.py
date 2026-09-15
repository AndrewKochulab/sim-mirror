# SPDX-License-Identifier: Apache-2.0
"""Finding the running daemon by its file, never trusting a stale one, and starting one detached."""

from __future__ import annotations

import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror.daemon.lifecycle import DaemonInfo, info_path, read_info, remove_info, start_detached, write_info
from sim_mirror.testing.fakes import FakeProcess

INFO = DaemonInfo(pid=4242, port=7466, version="0.1.0")


def test_a_running_daemon_is_found_by_its_private_file(tmp_path: Path) -> None:
    write_info(tmp_path, INFO)
    assert stat.S_IMODE(info_path(tmp_path).stat().st_mode) == 0o600
    assert INFO.url == "http://127.0.0.1:7466"
    assert read_info(tmp_path, alive=lambda pid: pid == 4242) == INFO
    assert read_info(tmp_path, alive=lambda pid: False) is None
    assert read_info(tmp_path / "nowhere") is None


@pytest.mark.parametrize(
    "odd",
    [
        "not json",
        "[]",
        '{"pid": "4242", "port": 7466, "version": "0.1.0"}',
        '{"pid": 4242, "port": 0, "version": "0.1.0"}',
        '{"pid": true, "port": 7466, "version": "0.1.0"}',
        '{"pid": 4242, "port": 7466, "version": 1}',
    ],
)
def test_a_file_that_does_not_name_a_daemon_is_no_daemon(tmp_path: Path, odd: str) -> None:
    info_path(tmp_path).write_text(odd)
    assert read_info(tmp_path, alive=lambda pid: True) is None


def test_the_file_is_removed_only_by_the_daemon_it_names(tmp_path: Path) -> None:
    assert remove_info(tmp_path, 4242) is False
    write_info(tmp_path, INFO)
    assert remove_info(tmp_path, 1) is False and info_path(tmp_path).exists()
    assert remove_info(tmp_path, 4242) is True and not info_path(tmp_path).exists()
    info_path(tmp_path).write_text("[]")
    assert remove_info(tmp_path, 4242) is False


async def test_a_detached_daemon_is_started_with_its_log_and_answers_its_pid(tmp_path: Path) -> None:
    started: list[tuple[tuple[str, ...], Path]] = []

    async def spawn(argv: Sequence[str], log_path: Path) -> FakeProcess:
        started.append((tuple(argv), log_path))
        return FakeProcess(pid=5151)

    assert await start_detached(["sim-mirror", "serve"], tmp_path / "daemon.log", spawn=spawn) == 5151
    assert started == [(("sim-mirror", "serve"), tmp_path / "daemon.log")]
