# SPDX-License-Identifier: Apache-2.0
"""What every helper kind shares, seen from a kind that is not idb_companion: its own folder, name, log and timeout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, Screen
from sim_mirror.connectors.helper_process import HelperProcesses, HelperSpec, RunningHelper, helper_id, recorded_helpers

UDID = "D946616B-6E4F-4F5C-8C76-54FAD9B7D702"
TAG = "SimMirror"


class Refused(ConnectorUnavailable):
    pass


class Process:
    pid = 5150
    returncode: int | None = None

    async def wait(self) -> int | None:
        return self.returncode


class Silent:
    async def describe(self) -> Screen:
        raise ConnectorError("still loading")

    async def close(self) -> None:
        return None


def launcher(tmp_path: Path, spawned: list[tuple[tuple[str, ...], Path]]) -> HelperProcesses[Silent]:
    now = [0.0]
    folder = tmp_path / "run" / "native"

    async def spawn(argv: Sequence[str], log: Path, /, *, env: Mapping[str, str] | None = None) -> Process:
        spawned.append((tuple(argv), log))
        (folder / f"{helper_id(UDID)}.sock").touch()
        return Process()

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    return HelperProcesses(
        HelperSpec(program="sim-mirror-helper", log_prefix="native", unavailable=Refused, ready_timeout_s=3),
        folder=folder,
        log_dir=tmp_path / "logs",
        owner_tag=TAG,
        connect=lambda path: Silent(),
        spawn=spawn,
        signal_group=lambda pid, sig: None,
        pid_alive=lambda pid: False,
        clock=lambda: now[0],
        sleep=sleep,
        owner=777,
    )


async def test_a_helper_kind_keeps_its_own_folder_log_name_timeout_and_refusal(tmp_path: Path) -> None:
    spawned: list[tuple[tuple[str, ...], Path]] = []
    helpers = launcher(tmp_path, spawned)
    socket = helpers.socket_for(UDID)
    assert socket == tmp_path / "run" / "native" / f"{helper_id(UDID)}.sock"
    with pytest.raises(Refused, match="sim-mirror-helper did not answer within 3 seconds: still loading") as caught:
        await helpers.launch(lambda path: ("helper", "--socket", str(path)), UDID)
    assert caught.value.status == 504
    assert spawned == [(("helper", "--socket", str(socket)), tmp_path / "logs" / f"native-{helper_id(UDID)}.log")]
    with pytest.raises(Refused, match=r"within 0\.4 seconds"):
        await helpers.launch(lambda path: ("helper",), UDID, ready_timeout_s=0.4)
    assert recorded_helpers(socket.parent) == []


def test_a_running_helper_is_alive_until_its_process_exits() -> None:
    process = Process()
    running = RunningHelper(UDID, process, Path("s"), Path("p"), Silent())
    assert running.alive and running.developer_dir == ""
    process.returncode = 0
    assert not running.alive
