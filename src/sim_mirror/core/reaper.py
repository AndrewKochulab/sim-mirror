# SPDX-License-Identifier: Apache-2.0
"""The loop that runs `DeviceManager.reap` once a minute, for as long as a host runs one -- and after it, each job added
(ending the builds settings no longer allow), each on its own -- and beside it the one that runs
`DeviceManager.improve` round after round, so a real device's cable plugged back in is used within moments.

That one also notices the process's event loop held up -- by work done on it rather than on a thread -- and logs it:
while the loop is held, an answer that has already come is not heard, so a reading or a request waiting on it times out
though nothing it waited for was slow, and the log is where that shows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sim_mirror.core.manager import DeviceManager

logger = logging.getLogger(__name__)

REAP_EVERY_S = 60.0
#: How long after one round of asking running devices whether they could be reached better the next starts. A real
#: device's round listens to usbmuxd for a while, on a thread, so its cable is heard of as it goes in.
IMPROVE_EVERY_S = 1.0
#: How much later than asked the improving loop may wake before the event loop counts as held up.
HELD_S = 1.0


class Reaper:
    def __init__(
        self,
        manager: DeviceManager,
        *,
        every_s: float = REAP_EVERY_S,
        improve_every_s: float = IMPROVE_EVERY_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._every_s = every_s
        self._improve_every_s = improve_every_s
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._improving: asyncio.Task[None] | None = None
        self._jobs: list[Callable[[], Awaitable[Any]]] = []

    def add(self, job: Callable[[], Awaitable[Any]]) -> None:
        """Run `job` after every reap."""
        self._jobs.append(job)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._loop())
            self._improving = asyncio.get_running_loop().create_task(self._improve())

    async def _loop(self) -> None:
        while True:
            await self._sleep(self._every_s)
            try:
                await self._manager.reap()
            except Exception:
                logger.exception("reaping simulators failed; trying again in %gs", self._every_s)
            for job in self._jobs:
                try:
                    await job()
                except Exception:
                    logger.exception("a reaper job failed; trying again in %gs", self._every_s)

    async def _improve(self) -> None:
        while True:
            asleep = self._clock()
            await self._sleep(self._improve_every_s)
            late = self._clock() - asleep - self._improve_every_s
            if late >= HELD_S:
                logger.warning(
                    "the event loop was held for %.1fs: a reading or a request waiting on it then may have timed out "
                    "though its answer had come",
                    late,
                )
            try:
                await self._manager.improve()
            except Exception:
                logger.exception("asking devices whether they can be reached better failed")

    async def stop(self) -> None:
        for task in (self._task, self._improving):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._improving = None
