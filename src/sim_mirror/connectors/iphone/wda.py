# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent on the devices SimMirror drives: found running, or started from what its setup built (`wda_setup`).

A session asks for it when it attaches (`WdaService.client`). If WebDriverAgent already answers on the device's
loopback -- kept running from before, or started by the person -- it is used as it is. Otherwise it is started from the
build for the device's team and Xcode, and waited for up to `real_devices.wda.startup_timeout`; the first start on a
device waits while iOS asks the person to trust the developer and allow UI automation. With no build yet, a person sets
it up once (the viewer's Set up touch, or ``sim-mirror wda setup``); after that a new Xcode or another device is built
for in the background, and the device is attached again when it is ready. A WebDriverAgent SimMirror
started ends when the device is let go, unless `real_devices.wda.keep_running` says to leave it; one a crashed host left
behind is ended the next time the host starts.

Everything is kept under SimMirror's state folder: the source (`wda_source`), a build per team and Xcode, and the pid
file of each run; each run's output goes to the host's log folder.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.build.wda import start_wda
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.connectors.helper_process import end_group, helper_id, read_pid_file, runs_program
from sim_mirror.connectors.iphone.wda_client import Opener, WdaClient, usbmux_opener
from sim_mirror.connectors.iphone.wda_setup import WdaSetup
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform import process
from sim_mirror.storage.private import ensure_private_dir

logger = logging.getLogger(__name__)

#: How often a starting WebDriverAgent is asked whether it answers.
POLL_S = 1.0
#: What a run of WebDriverAgent is, as the system names its process.
PROGRAM = "xcodebuild"
#: The last lines of a run's log a refusal quotes.
LOG_TAIL = 3


@dataclass
class Run:
    """A WebDriverAgent SimMirror started on a device."""

    udid: str
    process: Any
    pid_file: Path
    log: Path


class WdaService:
    """Starts, finds, ends and cleans up after WebDriverAgent. The keyword arguments after `copy` are test seams."""

    def __init__(
        self,
        *,
        run_dir: Path,
        log_dir: Path,
        owner_tag: str,
        copy: HostCopy | None = None,
        setup: WdaSetup | None = None,
        opener_for: Callable[[str], Opener] = usbmux_opener,
        start: Callable[..., Awaitable[Any]] = start_wda,
        signal_group: Callable[[int, int], None] = process.signal_group,
        pid_alive: Callable[[int], bool] = process.pid_alive,
        command_of: Callable[[int], Awaitable[str | None]] = process.command_of,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner: int | None = None,
        ensure_dir: Callable[[Path], Path] = ensure_private_dir,
    ) -> None:
        self._folder = run_dir / "wda"
        self._log_dir = log_dir
        self._tag = owner_tag
        self._copy = copy or HostCopy()
        self._setup = setup or WdaSetup()
        self._opener_for = opener_for
        self._start = start
        self._signal = signal_group
        self._pid_alive = pid_alive
        self._command_of = command_of
        self._clock = clock
        self._sleep = sleep
        self._owner = os.getpid() if owner is None else owner
        self._ensure_dir = ensure_dir
        self._runs: dict[str, Run] = {}

    def client_for(self, udid: str) -> WdaClient:
        return WdaClient(self._opener_for(udid))

    async def client(self, udid: str, config: SimConfig, developer_dir: str) -> WdaClient:
        """WebDriverAgent on the device, answering: found running, else started. Refuses with what to do."""
        client = self.client_for(udid)
        if await client.status() is not None:
            return client
        team = config.real_devices_team_id
        if not team:
            raise ConnectorUnavailable(self._copy.wda_needs_team(), 409)
        test_run = self._setup.built_for(team, developer_dir, udid)
        if test_run is None:
            raise ConnectorUnavailable(self._not_built(udid, team, developer_dir, config.wda_path), 409)
        await self.stop(udid)
        folder = self._ensure_dir(self._folder)
        log = self._log_dir / f"wda-{helper_id(udid)}.log"
        try:
            started = await self._start(test_run, udid, team, developer_dir, log)
        except OSError as exc:
            raise ConnectorUnavailable(f"WebDriverAgent could not be started: {exc}") from exc
        pid_file = folder / f"{helper_id(udid)}.pid"
        pid_file.write_text(f"{started.pid} {self._owner} {self._tag}", encoding="utf-8")
        run = Run(udid, started, pid_file, log)
        self._runs[udid] = run
        try:
            await self._answering(client, run, config.wda_startup_timeout)
        except (ConnectorUnavailable, asyncio.CancelledError):
            await self.stop(udid)
            raise
        logger.info("started WebDriverAgent on %s (pid %s)", udid, started.pid)
        return client

    def _not_built(self, udid: str, team: str, developer_dir: str, source_path: str) -> str:
        """Why WebDriverAgent is not there for the device yet -- building it when a person has set it up for the team
        before -- and what a person can do."""
        setup = self._setup.state(team, developer_dir)
        if setup is not None and setup.state == "failed":
            return self._copy.wda_setup_failed(setup.said)
        if setup is None or setup.state == "ready":
            if not self._setup.agreed(team):
                return self._copy.wda_offer(udid)
            self._setup.start(team, developer_dir, udid, source_path)
        return self._copy.wda_building(team)

    async def _answering(self, client: WdaClient, run: Run, timeout_s: float) -> None:
        deadline = self._clock() + timeout_s
        while await client.status() is None:
            if run.process.returncode is not None:
                raise ConnectorUnavailable(self._copy.wda_ended(_tail(run.log), str(run.log), run.udid), 409)
            if self._clock() >= deadline:
                raise ConnectorUnavailable(self._copy.wda_slow(round(timeout_s), str(run.log)), 504)
            await self._sleep(POLL_S)

    def alive(self, udid: str) -> bool:
        """Whether the WebDriverAgent SimMirror started on the device still runs -- True when it started none, since
        one it found running is not its to watch."""
        run = self._runs.get(udid)
        return run is None or run.process.returncode is None

    async def stop(self, udid: str) -> None:
        """End the WebDriverAgent SimMirror started on the device, if it did."""
        run = self._runs.pop(udid, None)
        if run is None:
            return
        pid = run.process.pid
        await end_group(
            pid,
            run.process,
            running=lambda: run.process.returncode is None,
            signal_group=self._signal,
            clock=self._clock,
            sleep=self._sleep,
        )
        with contextlib.suppress(FileNotFoundError):
            run.pid_file.unlink()

    async def reap_orphans(self) -> int:
        """End every WebDriverAgent this host left running when it went away. Answers how many there were."""
        ended = 0
        for pid_file in sorted(self._ensure_dir(self._folder).glob("*.pid")):
            recorded = read_pid_file(pid_file)
            if recorded is None or recorded.tag != self._tag:
                continue
            if recorded.owner is not None and recorded.owner != self._owner and self._pid_alive(recorded.owner):
                continue
            if await runs_program(recorded.pid, PROGRAM, self._pid_alive, self._command_of):
                logger.info("ending WebDriverAgent a previous run left behind (pid %s)", recorded.pid)
                await self._end_left(recorded.pid)
                ended += 1
            with contextlib.suppress(FileNotFoundError):
                pid_file.unlink()
        return ended

    async def _end_left(self, pid: int) -> None:
        def running() -> bool:
            return self._pid_alive(pid)

        await end_group(pid, None, running=running, signal_group=self._signal, clock=self._clock, sleep=self._sleep)

    async def shutdown(self) -> None:
        for udid in list(self._runs):
            await self.stop(udid)


def _tail(log: Path) -> str:
    """The last lines a run wrote, which say why it ended."""
    try:
        said = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in said.splitlines() if line.strip()]
    return " ".join(lines[-LOG_TAIL:])
