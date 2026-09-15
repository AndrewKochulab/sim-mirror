# SPDX-License-Identifier: Apache-2.0
"""Waiting after an agent's steps: for text to appear, for it to go, or for the screen to settle.

A wait is asked for as one of ``{"for": text}``, ``{"gone": text}`` or ``{"settle_ms": ms}``, with an optional
``timeout_ms``. `parse_wait` checks it -- a wait asked for wrongly is skipped with why, never guessed at -- and `Waiter`
runs it, answering one line: how long it waited, or that it gave up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sim_mirror.perception.settle import POLL_S, SettleDetector
from sim_mirror.perception.snapshot import Snapshot
from sim_mirror.validation import Invalid, whole

TIMEOUT_MS = (100, 10000)
DEFAULT_TIMEOUT_MS = 5000
SETTLE_MS = (100, 3000)
SHAPE = 'give one of {"for": text}, {"gone": text} or {"settle_ms": ms}'


@dataclass(frozen=True)
class Wait:
    kind: Literal["for", "gone", "settle"]
    timeout_s: float
    text: str = ""
    quiet_s: float = 0.0


def parse_wait(spec: object) -> Wait:
    """The wait a call asked for. Raises `Invalid` with what would do."""
    if not isinstance(spec, dict) or sum(key in spec for key in ("for", "gone", "settle_ms")) != 1:
        raise Invalid(SHAPE)
    timeout_s = whole(spec.get("timeout_ms"), DEFAULT_TIMEOUT_MS, TIMEOUT_MS, "timeout_ms") / 1000
    if "settle_ms" in spec:
        return Wait("settle", timeout_s, quiet_s=whole(spec["settle_ms"], 0, SETTLE_MS, "settle_ms") / 1000)
    kind: Literal["for", "gone"] = "for" if "for" in spec else "gone"
    text = spec[kind]
    if not isinstance(text, str) or not text:
        raise Invalid("wait takes text to look for")
    return Wait(kind, timeout_s, text=text)


class Waiter:
    def __init__(
        self,
        *,
        read: Callable[[], Awaitable[Snapshot]],
        settle: SettleDetector,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._read = read
        self._settle = settle
        self._clock = clock
        self._sleep = sleep

    async def run(self, wait: Wait) -> str:
        if wait.kind == "settle":
            return await self._settle.settle(wait.quiet_s, wait.timeout_s)
        present = wait.kind == "for"
        started = self._clock()
        while True:
            found = (await self._read()).find(wait.text) is not None
            waited = round((self._clock() - started) * 1000)
            if found == present:
                return f'waited {waited}ms for "{wait.text}"' + ("" if present else " to go")
            if self._clock() - started >= wait.timeout_s:
                return f'still {"no" if present else "showing"} "{wait.text}" after {waited}ms'
            await self._sleep(POLL_S)
