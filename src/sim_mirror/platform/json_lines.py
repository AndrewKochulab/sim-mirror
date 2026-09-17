# SPDX-License-Identifier: Apache-2.0
"""A child process spoken to in JSON, one message a line each way: Xcode's ``mcpbridge``, SimMirror's own helpers.

`JsonLines` starts the program with pipes, in a process group of its own, and matches each answer to its request by
``id``. It keeps what the program last said on stderr, to say why it stopped. A request not answered in time ends the
program -- one that stopped answering is not trusted with the next request -- and a program that stops fails every
request still waiting, with why. It never decides what to say; its callers do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
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

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._stopped()

    def _stopped(self) -> bool:
        return bool(self._tasks) and self._tasks[0].done()

    async def start(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        """Start the program, ending one this started before."""
        await self.close()
        try:
            self._process = await self._spawn(argv, env)
        except OSError as exc:
            raise self._error(f"{self.name} could not be started: {exc}") from exc
        self._stderr.clear()
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
        waiting: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = waiting
        try:
            await self.send({**message, "id": request_id})
            return await asyncio.wait_for(asyncio.shield(waiting), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # The request is let go of first, so closing does not fail it again where nobody hears.
            self._pending.pop(request_id, None)
            await self.close()
            raise self._error(f"{self._answerer} did not answer {what} within {timeout:g} seconds") from None
        finally:
            self._pending.pop(request_id, None)

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
