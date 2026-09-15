# SPDX-License-Identifier: Apache-2.0
"""The loop that runs `DeviceManager.reap` once a minute, for as long as a host runs one."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from sim_mirror.core.manager import DeviceManager

logger = logging.getLogger(__name__)

REAP_EVERY_S = 60.0


class Reaper:
    def __init__(
        self,
        manager: DeviceManager,
        *,
        every_s: float = REAP_EVERY_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._manager = manager
        self._every_s = every_s
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await self._sleep(self._every_s)
            try:
                await self._manager.reap()
            except Exception:
                logger.exception("reaping simulators failed; trying again in %gs", self._every_s)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
