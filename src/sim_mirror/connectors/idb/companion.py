# SPDX-License-Identifier: Apache-2.0
"""idb_companion: found, started and ended only here -- one per booted device.

The companion is what makes a device visible and touchable (`engine.py`). It is a long-running program holding a
device's framebuffer open, so it is:

* **found** where a person put it: the configured path, else on PATH, else where Homebrew installs it
  (`find_companion`);
* **started in a process group of its own** (`platform.process.spawn`), its output in the host's log folder;
* **run with the scope's Xcode**, named in ``DEVELOPER_DIR`` (`platform.developer_dir`): left to itself it uses
  whatever ``xcode-select`` names, which is the whole machine's and need not be the Xcode the scope's simctl uses --
  so a device could be shown and touched through one Xcode's SimulatorKit while it was booted with another's;
* **serving on a unix socket** in the host's run folder (0700) -- no TCP port anything else on the machine could reach,
  and a path short enough for the 104 bytes a socket path may have;
* **given a pid file beside the socket** naming the companion, the process that started it and that process's host
  (its owner tag), so a companion a crashed host left running is found and ended the next time that host starts
  (`reap_orphans`) -- only while that pid still runs a companion, never while the process that owns it is alive, and
  never one another host started. A second line names the Xcode it runs with, so what is running can be reported;
* **ready when it answers ``describe``**, which fails fast when the device is not booted or the Mac has no GUI session;
* **ended by signalling its group**: TERM, then KILL.

`scripts/check_containment.py` keeps every other module from starting one.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, InputSink, ScreenReader, ScreenSource
from sim_mirror.connectors.idb.engine import IdbEngine
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform import process
from sim_mirror.platform.developer_dir import developer_env
from sim_mirror.platform.process import Runner
from sim_mirror.storage.private import ensure_private_dir

logger = logging.getLogger(__name__)

PROGRAM = "idb_companion"
#: Where Homebrew puts it on Apple silicon and on Intel.
COMPANION_CANDIDATES = ("/opt/homebrew/bin/idb_companion", "/usr/local/bin/idb_companion")

READY_TIMEOUT_S = 15.0
READY_POLL_S = 0.2
STOP_TIMEOUT_S = 5.0
STOP_POLL_S = 0.1


class CompanionEngine(ScreenSource, InputSink, ScreenReader, Protocol):
    """Everything a companion's engine is: its screen, its input, its reader, and letting go of it."""

    async def close(self) -> None: ...


Connect = Callable[[str], CompanionEngine]


class Spawn(Protocol):
    """How a companion is started: `process.spawn`, or a test's stand-in."""

    def __call__(self, argv: Sequence[str], log: Path, /, *, env: Mapping[str, str] | None = ...) -> Awaitable[Any]: ...


class CompanionUnavailable(ConnectorUnavailable):
    """A companion that could not be had, with the HTTP status it means."""


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
    return hashlib.sha256(udid.encode("utf-8")).hexdigest()[:12]


def companion_argv(binary: str, udid: str, socket: Path) -> tuple[str, ...]:
    return (binary, "--udid", udid, "--grpc-domain-sock", str(socket), "--log-level", "info")


@dataclass
class Companion:
    udid: str
    process: Any
    socket: Path
    pid_file: Path
    engine: CompanionEngine
    #: The Xcode it runs with, as its developer folder; empty when nothing named one and it chose for itself.
    developer_dir: str = ""

    @property
    def alive(self) -> bool:
        return bool(self.process.returncode is None)


@dataclass(frozen=True)
class Recorded:
    """What a pid file says: the companion, the process that started it, that process's host, and its Xcode."""

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


def recorded_companions(run_dir: Path) -> list[Recorded]:
    """What every readable pid file in a run folder names, whether or not its companion still runs."""
    found = (read_pid_file(pid_file) for pid_file in sorted(run_dir.glob("*.pid")))
    return [record for record in found if record is not None]


async def runs_companion(
    pid: int, pid_alive: Callable[[int], bool], command_of: Callable[[int], Awaitable[str | None]]
) -> bool:
    """Whether `pid` is a live companion -- not a pid the system has since given to something else."""
    return pid > 1 and pid_alive(pid) and PROGRAM in (await command_of(pid) or "")


class CompanionLauncher:
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
        if not owner_tag or any(ch.isspace() for ch in owner_tag):
            raise ValueError(f"an owner tag is one word: {owner_tag!r}")
        self._run_dir = run_dir
        self._log_dir = log_dir
        self._tag = owner_tag
        self._copy = copy or HostCopy()
        self._spawn = spawn
        self._connect = connect
        self._signal = signal_group
        self._pid_alive = pid_alive
        self._command_of = command_of
        self._clock = clock
        self._sleep = sleep
        #: The process this launcher speaks for, written into every pid file it makes.
        self._owner = os.getpid() if owner is None else owner
        self._ensure_dir = ensure_dir

    async def start(self, binary: str, udid: str, developer_dir: str = "") -> Companion:
        """A companion for this booted device, running with the Xcode at `developer_dir` and ready to answer.

        An empty `developer_dir` leaves the companion to find an Xcode itself. Refuses with a reason.
        """
        runs = self._ensure_dir(self._run_dir)
        name = companion_id(udid)
        socket, pid_file = runs / f"{name}.sock", runs / f"{name}.pid"
        other = await self._other_owner(pid_file)
        if other is not None:
            owner, tag = other
            raise CompanionUnavailable(self._copy.claimed(tag, owner), 409)
        await self._end_recorded(pid_file)
        self._tidy(socket)
        log = self._log_dir / f"idb-{name}.log"
        try:
            started = await self._spawn(companion_argv(binary, udid, socket), log, env=developer_env(developer_dir))
        except OSError as exc:
            raise CompanionUnavailable(f"idb_companion could not be started: {exc}") from exc
        record = f"{started.pid} {self._owner} {self._tag}" + (f"\n{developer_dir}" if developer_dir else "")
        pid_file.write_text(record, encoding="utf-8")
        try:
            engine = await self._ready(started, socket, log)
        except (CompanionUnavailable, asyncio.CancelledError):
            # Refused, or let go while starting -- a device stopped, switched off or chosen again mid-start: either way
            # the companion does not outlive this start, nor do its socket and pid file.
            await self._end(started.pid, started)
            self._tidy(socket, pid_file)
            raise
        logger.info(
            "started idb_companion for %s (pid %s) with %s", udid, started.pid, developer_dir or "its own Xcode"
        )
        return Companion(
            udid=udid, process=started, socket=socket, pid_file=pid_file, engine=engine, developer_dir=developer_dir
        )

    async def _ready(self, started: Any, socket: Path, log: Path) -> CompanionEngine:
        deadline = self._clock() + READY_TIMEOUT_S
        last: ConnectorError | None = None
        while True:
            if started.returncode is not None:
                raise CompanionUnavailable(
                    f"idb_companion exited as it started (exit {started.returncode}); its log is {log}"
                )
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
                raise CompanionUnavailable(
                    f"idb_companion did not answer within {READY_TIMEOUT_S:g} seconds{detail}", 504
                )
            await self._sleep(READY_POLL_S)

    async def stop(self, companion: Companion) -> None:
        """End a companion and everything it started; its socket and pid file go with it."""
        with contextlib.suppress(Exception):
            await companion.engine.close()
        await self._end(companion.process.pid, companion.process)
        self._tidy(companion.socket, companion.pid_file)

    async def reap_orphans(self) -> int:
        """End every companion this host left running when it went away. Answers how many there were.

        A companion another host started, or a live process of this host is running, is not an orphan: it and its
        files are left alone.
        """
        ended = 0
        for pid_file in sorted(self._ensure_dir(self._run_dir).glob("*.pid")):
            recorded = read_pid_file(pid_file)
            if recorded is not None and recorded.tag not in (None, self._tag):
                continue
            if await self._other_owner(pid_file) is not None:
                continue
            if await self._end_recorded(pid_file):
                ended += 1
            self._tidy(pid_file.with_suffix(".sock"), pid_file)
        return ended

    async def _runs_companion(self, pid: int) -> bool:
        return await runs_companion(pid, self._pid_alive, self._command_of)

    async def _other_owner(self, pid_file: Path) -> tuple[int, str] | None:
        """The pid and host of another live process running the companion this file names, or None."""
        recorded = read_pid_file(pid_file)
        if recorded is None or recorded.owner is None or not self._pid_alive(recorded.owner):
            return None
        if recorded.owner == self._owner and recorded.tag in (None, self._tag):
            return None
        if not await self._runs_companion(recorded.pid):
            return None
        return recorded.owner, recorded.tag or self._tag

    async def _end_recorded(self, pid_file: Path) -> bool:
        """End the companion a pid file names, if that pid still runs one."""
        recorded = read_pid_file(pid_file)
        if recorded is None or not await self._runs_companion(recorded.pid):
            return False
        logger.info("ending an idb_companion a previous run left behind (pid %s)", recorded.pid)
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
