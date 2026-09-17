# SPDX-License-Identifier: Apache-2.0
"""idb_companion: found, started and ended only here -- one per booted device.

The companion is what makes a device visible and touchable (`engine.py`). It is a long-running program holding a
device's framebuffer open, so it lives the life every helper does (`connectors.helper_process`): its own process group,
the scope's Xcode, a unix socket and pid file in the host's run folder, ready when it answers ``describe``, and a
companion a crashed host left running ended the next time that host starts. What is the companion's own is here:

* **found** where a person put it: the configured path, else on PATH, else where Homebrew installs it
  (`find_companion`);
* **run with its flags** (`companion_argv`), and **asked its version** (`companion_version`);
* **kept at the top of the run folder**, where SimMirror 1.0 kept them, so a companion a 1.0 host left behind is still
  found.

`scripts/check_containment.py` keeps every other module from starting one.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from sim_mirror.connectors.base import ConnectorUnavailable, InputSink, ScreenReader, ScreenSource
from sim_mirror.connectors.helper_process import (
    READY_POLL_S,
    READY_TIMEOUT_S,
    STOP_POLL_S,
    STOP_TIMEOUT_S,
    HelperProcesses,
    HelperSpec,
    Recorded,
    RunningHelper,
    Spawn,
    helper_id,
    read_pid_file,
    recorded_helpers,
    runs_program,
)
from sim_mirror.connectors.idb.engine import IdbEngine
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform import process
from sim_mirror.platform.process import Runner
from sim_mirror.storage.private import ensure_private_dir

__all__ = [
    "COMPANION_CANDIDATES",
    "PROGRAM",
    "READY_POLL_S",
    "READY_TIMEOUT_S",
    "STOP_POLL_S",
    "STOP_TIMEOUT_S",
    "Companion",
    "CompanionEngine",
    "CompanionLauncher",
    "CompanionUnavailable",
    "Recorded",
    "companion_argv",
    "companion_id",
    "companion_version",
    "find_companion",
    "read_pid_file",
    "recorded_companions",
    "runs_companion",
]

PROGRAM = "idb_companion"
#: Where Homebrew puts it on Apple silicon and on Intel.
COMPANION_CANDIDATES = ("/opt/homebrew/bin/idb_companion", "/usr/local/bin/idb_companion")


class CompanionEngine(ScreenSource, InputSink, ScreenReader, Protocol):
    """Everything a companion's engine is: its screen, its input, its reader, and letting go of it."""

    async def close(self) -> None: ...


Connect = Callable[[str], CompanionEngine]
Companion = RunningHelper[CompanionEngine]


class CompanionUnavailable(ConnectorUnavailable):
    """A companion that could not be had, with the HTTP status it means."""


SPEC = HelperSpec(program=PROGRAM, log_prefix="idb", unavailable=CompanionUnavailable)


def _runnable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_companion(
    configured: str,
    candidates: Sequence[str] = COMPANION_CANDIDATES,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """The companion to run: the configured one when it runs, else the first one installed.

    A configured path that cannot be run is not quietly replaced by another copy: the person named that one, and a
    status says it cannot be run.
    """
    if configured:
        return configured if _runnable(configured) else None
    on_path = which(PROGRAM)
    places: list[str] = [on_path] if on_path else []
    places.extend(candidates)
    return next((path for path in places if _runnable(path)), None)


async def companion_version(binary: str, run: Runner = process.run) -> str | None:
    """What a companion says of its version -- idb_companion prints its build date and time -- or None."""
    code, out = await run((binary, "--version"))
    if code != 0:
        return None
    try:
        data: object = json.loads(out)
    except ValueError:
        return out.strip() or None
    if isinstance(data, dict):
        said = " ".join(str(data[key]) for key in ("build_date", "build_time") if key in data)
        return said or None
    return out.strip() or None


def companion_id(udid: str) -> str:
    """A short, stable name for a device's socket, pid file and log."""
    return helper_id(udid)


def companion_argv(binary: str, udid: str, socket: Path) -> tuple[str, ...]:
    return (binary, "--udid", udid, "--grpc-domain-sock", str(socket), "--log-level", "info")


def recorded_companions(run_dir: Path) -> list[Recorded]:
    """What every readable pid file in a run folder names, whether or not its companion still runs."""
    return recorded_helpers(run_dir)


async def runs_companion(
    pid: int, pid_alive: Callable[[int], bool], command_of: Callable[[int], Awaitable[str | None]]
) -> bool:
    """Whether `pid` is a live companion -- not a pid the system has since given to something else."""
    return await runs_program(pid, PROGRAM, pid_alive, command_of)


class CompanionLauncher(HelperProcesses[CompanionEngine]):
    """Starts, ends and cleans up after companions. The keyword arguments after `copy` are the test seams."""

    def __init__(
        self,
        *,
        run_dir: Path,
        log_dir: Path,
        owner_tag: str,
        copy: HostCopy | None = None,
        spawn: Spawn = process.spawn,
        connect: Connect = IdbEngine.at,
        signal_group: Callable[[int, int], None] = process.signal_group,
        pid_alive: Callable[[int], bool] = process.pid_alive,
        command_of: Callable[[int], Awaitable[str | None]] = process.command_of,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner: int | None = None,
        ensure_dir: Callable[[Path], Path] = ensure_private_dir,
    ) -> None:
        super().__init__(
            SPEC,
            folder=run_dir,
            log_dir=log_dir,
            owner_tag=owner_tag,
            connect=connect,
            copy=copy,
            spawn=spawn,
            signal_group=signal_group,
            pid_alive=pid_alive,
            command_of=command_of,
            clock=clock,
            sleep=sleep,
            owner=owner,
            ensure_dir=ensure_dir,
        )
        self._run_dir = run_dir

    async def start(self, binary: str, udid: str, developer_dir: str = "") -> Companion:
        """A companion for this booted device, running with the Xcode at `developer_dir` and ready to answer.

        An empty `developer_dir` leaves the companion to find an Xcode itself. Refuses with a reason.
        """
        return await self.launch(lambda socket: companion_argv(binary, udid, socket), udid, developer_dir)
