# SPDX-License-Identifier: Apache-2.0
"""Helper programs in process groups of their own, ending those groups, and asking about a pid.

A companion and a build are long-running programs SimMirror starts and has to be able to end completely -- with
whatever they started in turn -- even after SimMirror itself restarted and knows one only by the pid in its pid file.
So each runs in a new session, which makes its pid its process group, and is stopped by signalling the group.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: How long a killed child is given to exit before it is left to the system.
REAP_SECONDS = 2.0
#: How long a short query such as ``ps`` may take.
QUERY_TIMEOUT_S = 5.0
#: ``rc`` for a program that could not be started, and for one that did not finish in time.
CANNOT_RUN = 127
TIMED_OUT = 124

#: How a short query is run: an argv in, its exit code and output out -- `run`, or a test's stand-in for it.
Runner = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]


async def spawn(
    argv: Sequence[str], log_path: Path, *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> Any:
    """Start a program detached, in its own process group, its output appended to its log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else dict(env),
        )


def signal_group(pid: int, sig: int) -> None:
    """Signal a process group; one that is already gone is not an error."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists; one another user owns counts."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def kill_and_reap(proc: Any) -> None:
    """Kill a child and wait for it, so it does not stay a zombie until something else reaps it."""
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(asyncio.TimeoutError, TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=REAP_SECONDS)


async def run(argv: Sequence[str], *, timeout: float = QUERY_TIMEOUT_S) -> tuple[int, str]:
    """Run a short query and answer its exit code and output. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return CANNOT_RUN, ""
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        await kill_and_reap(proc)
        return TIMED_OUT, ""
    return proc.returncode or 0, out.decode(errors="replace")


async def _ps(field: str, pid: int) -> str | None:
    code, out = await run(("ps", "-o", f"{field}=", "-p", str(pid)))
    return (out.strip() or None) if code == 0 else None


async def command_of(pid: int) -> str | None:
    """The command line a pid runs, or None -- so a pid from a pid file is signalled only while it is still ours."""
    return await _ps("command", pid)


async def start_time(pid: int) -> str | None:
    """When a pid's process started, as ``ps`` says it, or None: a reused pid has a different one."""
    return await _ps("lstart", pid)
