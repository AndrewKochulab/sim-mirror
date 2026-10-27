# SPDX-License-Identifier: Apache-2.0
"""What every helper kind shares, seen from a kind that is not idb_companion: its own folder, name, log and timeout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, Screen
from sim_mirror.connectors.helper_process import (
    HelperProcesses,
    HelperSpec,
    RunningHelper,
    helper_id,
    last_error_line,
    recorded_helpers,
)

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


class Answers:
    async def describe(self) -> Screen:
        return Screen(width_px=1206, height_px=2622, width_pt=402, height_pt=874, scale=3.0)

    async def close(self) -> None:
        return None


async def test_what_a_helper_says_went_wrong_after_it_was_ready_is_its_last_error(tmp_path: Path) -> None:
    folder = tmp_path / "run" / "native"
    log = tmp_path / "logs" / f"native-{helper_id(UDID)}.log"

    async def spawn(argv: Sequence[str], to: Path, /, *, env: Mapping[str, str] | None = None) -> Process:
        to.parent.mkdir(parents=True, exist_ok=True)
        to.write_text("[error] said by the run before\n[info] serving the screen of it\n")
        (folder / f"{helper_id(UDID)}.sock").touch()
        return Process()

    helpers = HelperProcesses(
        HelperSpec(program="sim-mirror-helper", log_prefix="native"),
        folder=folder,
        log_dir=tmp_path / "logs",
        owner_tag=TAG,
        connect=lambda path: Answers(),
        spawn=spawn,
        signal_group=lambda pid, sig: None,
        pid_alive=lambda pid: False,
        owner=777,
    )
    running = await helpers.launch(lambda path: ("helper",), UDID)
    assert running.log == log and running.last_error() is None, "what it said before it was ready is not news"
    with log.open("a") as file:
        file.write("[error] Test iPhone has sent no picture of its screen in 15 seconds: is it unlocked?\n")
        file.write("[info] still trying\n")
    assert running.last_error() == "Test iPhone has sent no picture of its screen in 15 seconds: is it unlocked?"
    with log.open("a") as file:
        file.write("[error] macOS has not let sim-mirror-helper use the Camera\n[error]\n")
    assert running.last_error() == "macOS has not let sim-mirror-helper use the Camera", "the last one that says"
    log.write_text("[error] a log started over is read from its top\n")
    assert running.last_error() == "a log started over is read from its top"
    log.unlink()
    assert running.last_error() is None
    assert RunningHelper(UDID, Process(), Path("s"), Path("p"), Silent()).last_error() is None, "no log, nothing said"
    assert last_error_line(tmp_path / "missing.log") is None
