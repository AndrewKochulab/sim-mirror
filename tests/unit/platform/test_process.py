# SPDX-License-Identifier: Apache-2.0
"""Helper processes in their own groups, ending them, and asking what a pid runs and when it started."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.platform import process


async def test_a_program_starts_in_its_own_session_logging_to_its_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    async def fake_exec(*argv: str, **options: Any) -> str:
        seen.update(argv=argv, **options)
        return "process"

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", fake_exec)
    log = tmp_path / "logs" / "program.log"
    assert await process.spawn(["program", "-v"], log) == "process"
    assert seen["argv"] == ("program", "-v") and seen["start_new_session"] is True and log.parent.is_dir()
    assert seen["cwd"] is None and seen["env"] is None
    await process.spawn(["program"], log, cwd=tmp_path, env={"DEVELOPER_DIR": "/X.app"})
    assert seen["cwd"] == str(tmp_path) and seen["env"] == {"DEVELOPER_DIR": "/X.app"}


def test_signalling_a_group_that_is_gone_or_not_ours_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        raise ProcessLookupError if pid == 1 else PermissionError

    monkeypatch.setattr(process.os, "killpg", killpg)
    process.signal_group(1, 15)
    process.signal_group(2, 9)
    assert sent == [(1, 15), (2, 9)]


def test_a_pid_is_alive_when_it_exists_whoever_owns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    assert process.pid_alive(os.getpid()) is True

    def kill(pid: int, sig: int) -> None:
        raise ProcessLookupError if pid == 5 else PermissionError

    monkeypatch.setattr(process.os, "kill", kill)
    assert process.pid_alive(5) is False and process.pid_alive(6) is True


class _Child:
    def __init__(self, *, gone: bool = False, hangs: bool = False) -> None:
        self.gone, self.hangs = gone, hangs
        self.killed = self.waited = False

    def kill(self) -> None:
        if self.gone:
            raise ProcessLookupError
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        if self.hangs:
            await asyncio.sleep(10)
        return 0


async def test_a_killed_child_is_waited_for_unless_it_is_gone_or_will_not_go(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _Child()
    await process.kill_and_reap(child)
    assert child.killed and child.waited
    gone = _Child(gone=True)
    await process.kill_and_reap(gone)
    assert not gone.waited
    monkeypatch.setattr(process, "REAP_SECONDS", 0.01)
    stuck = _Child(hangs=True)
    await process.kill_and_reap(stuck)
    assert stuck.killed


async def test_a_query_answers_its_code_and_output_and_never_raises(tmp_path: Path) -> None:
    assert await process.run((sys.executable, "-c", "print('hi')")) == (0, "hi\n")
    assert (await process.run((sys.executable, "-c", "import sys; sys.exit(3)")))[0] == 3
    assert await process.run((str(tmp_path / "missing"),)) == (process.CANNOT_RUN, "")
    slow = (sys.executable, "-c", "import time; time.sleep(5)")
    assert await process.run(slow, timeout=0.05) == (process.TIMED_OUT, "")


async def test_what_a_pid_runs_and_when_it_started_are_read_or_none() -> None:
    command = await process.command_of(os.getpid())
    assert command is not None and "python" in command.lower()
    assert await process.start_time(os.getpid()) is not None
    assert await process.command_of(99_999_999) is None and await process.start_time(99_999_999) is None
