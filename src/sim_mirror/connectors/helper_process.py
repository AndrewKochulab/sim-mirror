# SPDX-License-Identifier: Apache-2.0
"""A helper program run for one booted device: started, made ready, ended, and cleaned up after.

idb_companion and SimMirror's native helper are both long-running programs that hold a device open, so both live the
same life, and it is written once here. A connector names its program (`HelperSpec`) and says how to run it and how to
reach it once it runs; `HelperProcesses` does the rest:

* **started in a process group of its own** (`process.spawn`), its output in the host's log folder;
* **run with the scope's Xcode**, named in ``DEVELOPER_DIR`` (`developer_dir.developer_env`), never ``xcode-select``;
* **serving on a unix socket** in its folder under the host's run folder (0700) -- no TCP port anything else on the
  machine could reach, and a path short enough for the 104 bytes a socket path may have;
* **given a pid file beside the socket** naming the helper, the process that started it and that process's host (its
  owner tag), so a helper a crashed host left running is found and ended the next time that host starts
  (`reap_orphans`) -- only while that pid still runs the program, never while the process that owns it is alive, and
  never one another host started. A second line names the Xcode it runs with, so what is running can be reported;
* **ready when it answers ``describe``**, which fails fast when the device is not booted or the Mac has no GUI session;
* **ended by signalling its group**: TERM, then KILL.

Each kind of helper keeps its own folder, so one kind's cleanup never touches another's files.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, Screen
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform import process
from sim_mirror.platform.developer_dir import developer_env
from sim_mirror.storage.private import ensure_private_dir

logger = logging.getLogger(__name__)

READY_TIMEOUT_S = 15.0
READY_POLL_S = 0.2
STOP_TIMEOUT_S = 5.0
STOP_POLL_S = 0.1


class Reachable(Protocol):
    """What a running helper is reached through: asked for the screen to know it is ready, and let go of."""

    async def describe(self) -> Screen: ...

    async def close(self) -> None: ...


R = TypeVar("R", bound=Reachable)


class Spawn(Protocol):
    """How a helper is started: `process.spawn`, or a test's stand-in."""

    def __call__(self, argv: Sequence[str], log: Path, /, *, env: Mapping[str, str] | None = ...) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class HelperSpec:
    """A kind of helper program."""

    #: The program's name, as a person reads it and as ``ps`` shows it: a pid runs the helper while its command
    #: line has this in it.
    program: str
    #: The prefix of each helper's log file: ``idb`` makes ``idb-<id>.log``.
    log_prefix: str
    #: The refusal raised when a helper cannot be had.
    unavailable: type[ConnectorUnavailable] = ConnectorUnavailable
    #: How long a started helper has to answer.
    ready_timeout_s: float = READY_TIMEOUT_S


def helper_id(udid: str) -> str:
    """A short, stable name for a device's socket, pid file and log."""
    return hashlib.sha256(udid.encode("utf-8")).hexdigest()[:12]


@dataclass
class RunningHelper(Generic[R]):
    """A helper started for a device, and what it is reached through."""

    udid: str
    process: Any
    socket: Path
    pid_file: Path
    engine: R
    #: The Xcode it runs with, as its developer folder; empty when nothing named one and it chose for itself.
    developer_dir: str = ""

    @property
    def alive(self) -> bool:
        return bool(self.process.returncode is None)


@dataclass(frozen=True)
class Recorded:
    """What a pid file says: the helper, the process that started it, that process's host, and its Xcode."""

    pid: int
    owner: int | None
    tag: str | None
    developer_dir: str | None = None


def read_pid_file(pid_file: Path) -> Recorded | None:
    """What a pid file names, or None. One written before owners, owner tags or Xcodes names fewer of them.

    The Xcode is on a line of its own, since a developer folder's path may have spaces in it: a reader that splits the
    whole file still finds the pid, owner and tag first.
    """
    try:
        first, _, rest = pid_file.read_text(encoding="utf-8").partition("\n")
        fields = first.split()
        return Recorded(
            pid=int(fields[0]),
            owner=int(fields[1]) if len(fields) > 1 else None,
            tag=fields[2] if len(fields) > 2 else None,
            developer_dir=next(iter(rest.splitlines()), "") or None,
        )
    except (OSError, ValueError, IndexError):
        return None


def recorded_helpers(folder: Path) -> list[Recorded]:
    """What every readable pid file in a helper folder names, whether or not its helper still runs."""
    found = (read_pid_file(pid_file) for pid_file in sorted(folder.glob("*.pid")))
    return [record for record in found if record is not None]


async def runs_program(
    pid: int, program: str, pid_alive: Callable[[int], bool], command_of: Callable[[int], Awaitable[str | None]]
) -> bool:
    """Whether `pid` runs `program` -- not a pid the system has since given to something else."""
    return pid > 1 and pid_alive(pid) and program in (await command_of(pid) or "")


class HelperProcesses(Generic[R]):
    """Starts, ends and cleans up after one kind of helper. The keyword arguments after `copy` are the test seams."""

    def __init__(
        self,
        spec: HelperSpec,
        *,
        folder: Path,
        log_dir: Path,
        owner_tag: str,
        connect: Callable[[str], R],
        copy: HostCopy | None = None,
        spawn: Spawn = process.spawn,
        signal_group: Callable[[int, int], None] = process.signal_group,
        pid_alive: Callable[[int], bool] = process.pid_alive,
        command_of: Callable[[int], Awaitable[str | None]] = process.command_of,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner: int | None = None,
        ensure_dir: Callable[[Path], Path] = ensure_private_dir,
    ) -> None:
        if not owner_tag or any(ch.isspace() for ch in owner_tag):
            raise ValueError(f"an owner tag is one word: {owner_tag!r}")
        self._spec = spec
        self._folder = folder
        self._log_dir = log_dir
        self._tag = owner_tag
        self._connect = connect
        self._copy = copy or HostCopy()
        self._spawn = spawn
        self._signal = signal_group
        self._pid_alive = pid_alive
        self._command_of = command_of
        self._clock = clock
        self._sleep = sleep
        #: The process this launcher speaks for, written into every pid file it makes.
        self._owner = os.getpid() if owner is None else owner
        self._ensure_dir = ensure_dir

    def socket_for(self, udid: str) -> Path:
        """Where the helper for this device serves."""
        return self._folder / f"{helper_id(udid)}.sock"

    async def launch(
        self,
        argv: Callable[[Path], Sequence[str]],
        udid: str,
        developer_dir: str = "",
        *,
        ready_timeout_s: float | None = None,
    ) -> RunningHelper[R]:
        """A helper for this booted device, run as `argv(socket)` with the Xcode at `developer_dir`, ready to answer.

        An empty `developer_dir` leaves the helper to find an Xcode itself. Refuses with a reason.
        """
        name = self._spec.program
        folder = self._ensure_dir(self._folder)
        stem = helper_id(udid)
        socket, pid_file = folder / f"{stem}.sock", folder / f"{stem}.pid"
        other = await self._other_owner(pid_file)
        if other is not None:
            owner, tag = other
            raise self._spec.unavailable(self._copy.claimed(tag, owner), 409)
        await self._end_recorded(pid_file)
        self._tidy(socket)
        log = self._log_dir / f"{self._spec.log_prefix}-{stem}.log"
        try:
            started = await self._spawn(argv(socket), log, env=developer_env(developer_dir))
        except OSError as exc:
            raise self._spec.unavailable(f"{name} could not be started: {exc}") from exc
        record = f"{started.pid} {self._owner} {self._tag}" + (f"\n{developer_dir}" if developer_dir else "")
        pid_file.write_text(record, encoding="utf-8")
        timeout = self._spec.ready_timeout_s if ready_timeout_s is None else ready_timeout_s
        try:
            engine = await self._ready(started, socket, log, timeout)
        except (ConnectorUnavailable, asyncio.CancelledError):
            # Refused, or let go while starting -- a device stopped, switched off or chosen again mid-start: either way
            # the helper does not outlive this start, nor do its socket and pid file.
            await self._end(started.pid, started)
            self._tidy(socket, pid_file)
            raise
        logger.info("started %s for %s (pid %s) with %s", name, udid, started.pid, developer_dir or "its own Xcode")
        return RunningHelper(
            udid=udid, process=started, socket=socket, pid_file=pid_file, engine=engine, developer_dir=developer_dir
        )

    async def _ready(self, started: Any, socket: Path, log: Path, timeout: float) -> R:
        name = self._spec.program
        deadline = self._clock() + timeout
        last: ConnectorError | None = None
        while True:
            if started.returncode is not None:
                exited = f"exit {started.returncode}"
                raise self._spec.unavailable(f"{name} exited as it started ({exited}); its log is {log}")
            if socket.exists():
                engine = self._connect(str(socket))
                try:
                    await engine.describe()
                    return engine
                except ConnectorError as exc:
                    last = exc
                    await engine.close()
                except asyncio.CancelledError:
                    # Let go while it was being asked: its channel goes too, not only the process.
                    with contextlib.suppress(Exception):
                        await engine.close()
                    raise
            if self._clock() >= deadline:
                detail = f": {last}" if last else ""
                raise self._spec.unavailable(f"{name} did not answer within {timeout:g} seconds{detail}", 504)
            await self._sleep(READY_POLL_S)

    async def stop(self, running: RunningHelper[R]) -> None:
        """End a helper and everything it started; its socket and pid file go with it."""
        with contextlib.suppress(Exception):
            await running.engine.close()
        await self._end(running.process.pid, running.process)
        self._tidy(running.socket, running.pid_file)

    async def reap_orphans(self) -> int:
        """End every helper this host left running when it went away. Answers how many there were.

        A helper another host started, or a live process of this host is running, is not an orphan: it and its files
        are left alone.
        """
        ended = 0
        for pid_file in sorted(self._ensure_dir(self._folder).glob("*.pid")):
            recorded = read_pid_file(pid_file)
            if recorded is not None and recorded.tag not in (None, self._tag):
                continue
            if await self._other_owner(pid_file) is not None:
                continue
            if await self._end_recorded(pid_file):
                ended += 1
            self._tidy(pid_file.with_suffix(".sock"), pid_file)
        return ended

    async def _runs_helper(self, pid: int) -> bool:
        return await runs_program(pid, self._spec.program, self._pid_alive, self._command_of)

    async def _other_owner(self, pid_file: Path) -> tuple[int, str] | None:
        """The pid and host of another live process running the helper this file names, or None."""
        recorded = read_pid_file(pid_file)
        if recorded is None or recorded.owner is None or not self._pid_alive(recorded.owner):
            return None
        if recorded.owner == self._owner and recorded.tag in (None, self._tag):
            return None
        if not await self._runs_helper(recorded.pid):
            return None
        return recorded.owner, recorded.tag or self._tag

    async def _end_recorded(self, pid_file: Path) -> bool:
        """End the helper a pid file names, if that pid still runs one."""
        recorded = read_pid_file(pid_file)
        if recorded is None or not await self._runs_helper(recorded.pid):
            return False
        logger.info("ending %s a previous run left behind (pid %s)", self._spec.program, recorded.pid)
        await self._end(recorded.pid, None)
        return True

    def _running(self, pid: int, started: Any | None) -> bool:
        return started.returncode is None if started is not None else self._pid_alive(pid)

    async def _end(self, pid: int, started: Any | None) -> None:
        self._signal(pid, signal.SIGTERM)
        deadline = self._clock() + STOP_TIMEOUT_S
        while self._running(pid, started):
            if self._clock() >= deadline:
                self._signal(pid, signal.SIGKILL)
                break
            await self._sleep(STOP_POLL_S)
        if started is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(started.wait(), timeout=1.0)

    @staticmethod
    def _tidy(*paths: Path) -> None:
        for path in paths:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
