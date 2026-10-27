# SPDX-License-Identifier: Apache-2.0
"""A child process spoken to in JSON, one message a line each way: Xcode's ``mcpbridge``, SimMirror's own helpers.

`JsonLines` starts the program with pipes, in a process group of its own, and matches each answer to its request by
``id``. It keeps what the program last said on stderr, to say why it stopped. A request not answered in time ends the
program -- one that stopped answering is not trusted with the next request -- and a program that stops fails every
request still waiting, with why -- and `timed_out` says which of the two ended it, so a caller does not take a
program it ended for being slow for one that keeps crashing. It never decides what to say; its callers do.

**A request not answered in time leaves what tells why** (`Silence`, as `silence`): whether the program was still
running, how many requests were waiting on it, how long since it last said anything, and how long the event loop was
held while the request waited. A loop held is a daemon too busy to hear an answer that came; a program running that
has said nothing for longer than the wait, with requests queued behind, is a program stuck on one. To know the loop's
part, a tiny task looks at the clock every `TICK_S` while a request waits -- and only then.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sim_mirror.platform.process import kill_and_reap

#: The longest line an answer may be. A longer one stops the program: nothing after it can be matched up.
LINE_MAX = 1024 * 1024
#: How many of the program's last lines on stderr are kept, to say why it stopped.
STDERR_LINES = 5
#: How long a stopped program's last words on stderr are waited for.
STDERR_WAIT_S = 0.5
#: How long the program is given to end once its stdin is closed.
CLOSE_S = 2.0
#: How often the event loop's clock is looked at while a request waits: how late it wakes is how long the loop was held.
TICK_S = 0.25


class LinesError(Exception):
    """The program could not be started, stopped, or did not answer."""


class LineProcess(Protocol):
    """The part of an `asyncio.subprocess.Process` `JsonLines` uses."""

    stdin: Any
    stdout: Any
    stderr: Any

    @property
    def returncode(self) -> int | None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


Spawn = Callable[[Sequence[str], Mapping[str, str]], Awaitable[LineProcess]]


@dataclass(frozen=True)
class Silence:
    """What was known when a request went unanswered for as long as it was given."""

    #: Whether the program was still running.
    alive: bool
    #: How many requests were waiting on it, this one among them.
    waiting: int
    #: How long since it last said anything, in seconds; None when it said nothing since it started.
    since_answer: float | None
    #: The longest the event loop was held while the request waited, in seconds.
    held: float


async def spawn_lines(argv: Sequence[str], env: Mapping[str, str]) -> LineProcess:
    """Start a program with pipes, in a process group of its own."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env),
        limit=LINE_MAX,
        start_new_session=True,
    )


class JsonLines:
    """One running program, started by `start` and ended by `close`.

    `name` is how the program is named when it stops; `answerer` who is said not to have answered in time. Every
    failure is raised as `error`, so a caller's own kind of error reaches its callers.
    """

    def __init__(
        self,
        name: str,
        *,
        spawn: Spawn = spawn_lines,
        error: Callable[[str], Exception] = LinesError,
        answerer: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._spawn = spawn
        self._error = error
        self._answerer = answerer or name
        self._process: LineProcess | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._tasks: list[asyncio.Task[None]] = []
        #: Whether this ended the program because it did not answer in time, rather than the program stopping by
        #: itself: until the program is started again.
        self.timed_out = False
        #: What was known when a request last went unanswered in time.
        self.silence: Silence | None = None
        self._clock = clock
        #: When the program last said anything, on `clock`.
        self._said_at: float | None = None
        #: For each request waiting: when it began, and the longest the event loop has been held since, on the loop's
        #: own clock.
        self._held: dict[int, tuple[float, float]] = {}
        self._ticker: asyncio.Task[None] | None = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._stopped()

    def _stopped(self) -> bool:
        return bool(self._tasks) and self._tasks[0].done()

    async def start(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        """Start the program, ending one this started before."""
        await self.close()
        self.timed_out = False
        try:
            self._process = await self._spawn(argv, env)
        except OSError as exc:
            raise self._error(f"{self.name} could not be started: {exc}") from exc
        self._stderr.clear()
        self._said_at = None
        self._tasks = [
            asyncio.create_task(self._read_answers(self._process)),
            asyncio.create_task(self._read_stderr(self._process)),
        ]

    async def send(self, message: Mapping[str, Any]) -> None:
        """Write one message, expecting no answer."""
        process = self._process
        if process is None:
            raise self._error(self.why_stopped())
        try:
            process.stdin.write(json.dumps(message).encode() + b"\n")
            await process.stdin.drain()
        except (OSError, RuntimeError) as exc:
            raise self._error(self.why_stopped()) from exc

    async def request(self, message: Mapping[str, Any], *, timeout: float, what: str) -> dict[str, Any]:
        """Write one message with an ``id`` of its own, and answer the message that answers it."""
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        waiting: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = waiting
        asked = loop.time()
        self._watch(request_id, asked)
        try:
            await self.send({**message, "id": request_id})
            asked = loop.time()
            return await asyncio.wait_for(asyncio.shield(waiting), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # How late the time-out itself came is the loop held too, as its last tick may not have woken yet.
            held = max(self._held[request_id][1], loop.time() - asked - timeout)
            said_at = self._said_at
            since = None if said_at is None else self._clock() - said_at
            silence = Silence(self.alive, len(self._pending), since, max(0.0, held))
            # The request is let go of first, so closing does not fail it again where nobody hears.
            self._pending.pop(request_id, None)
            self.timed_out = True
            await self.close()
            # Kept as the last thing before the error, so the caller reads this request's, not one that timed out as
            # this one closed.
            self.silence = silence
            raise self._error(f"{self._answerer} did not answer {what} within {timeout:g} seconds") from None
        finally:
            self._pending.pop(request_id, None)
            self._unwatch(request_id)

    def _watch(self, request_id: int, began: float) -> None:
        """Look at the event loop while this request waits."""
        self._held[request_id] = (began, 0.0)
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.get_running_loop().create_task(self._tick())

    def _unwatch(self, request_id: int) -> None:
        self._held.pop(request_id, None)
        if not self._held and self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    async def _tick(self) -> None:
        """While any request waits: how much later than asked the loop woke this, kept for each request waiting as
        the longest it has been held -- no more than since it began."""
        loop = asyncio.get_running_loop()
        while self._held:
            asleep = loop.time()
            await asyncio.sleep(TICK_S)
            woke = loop.time()
            late = woke - asleep - TICK_S
            for request_id, (began, held) in list(self._held.items()):
                self._held[request_id] = (began, max(held, min(late, woke - began)))

    async def close(self) -> None:
        """End the program: its stdin closed, then killed if it does not go. Closing twice is closing once."""
        process, self._process = self._process, None
        if process is not None:
            with contextlib.suppress(OSError, RuntimeError):
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=CLOSE_S)
            except (asyncio.TimeoutError, TimeoutError):
                await kill_and_reap(process)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        self._fail_pending(f"{self.name} was closed")

    def why_stopped(self) -> str:
        last = self._stderr[-1] if self._stderr else ""
        return f"{self.name} stopped{': ' + last if last else ''}"

    async def _read_answers(self, process: LineProcess) -> None:
        try:
            while line := await process.stdout.readline():
                self._said_at = self._clock()
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
                    answers = message.get("id") if isinstance(message, dict) else None
                    waiting = self._pending.get(answers) if isinstance(answers, int) else None
                    if waiting is not None and not waiting.done():
                        waiting.set_result(message)
        except (ValueError, asyncio.LimitOverrunError, OSError):
            # A line longer than LINE_MAX: nothing after it can be matched up, so the program counts as stopped.
            pass
        # What the program said on its way out is usually on stderr, which may still be being read.
        await asyncio.wait(self._tasks[1:], timeout=STDERR_WAIT_S)
        self._fail_pending(self.why_stopped())

    async def _read_stderr(self, process: LineProcess) -> None:
        with contextlib.suppress(ValueError, asyncio.LimitOverrunError, OSError):
            while line := await process.stderr.readline():
                text = line.decode(errors="replace").strip()
                if text:
                    self._stderr.append(text)

    def _fail_pending(self, why: str) -> None:
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.set_exception(self._error(why))
