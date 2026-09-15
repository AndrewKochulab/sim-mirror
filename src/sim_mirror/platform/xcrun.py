# SPDX-License-Identifier: Apache-2.0
"""The one place SimMirror runs ``xcrun`` -- simctl, xcodebuild, xcresulttool.

* An argument vector, never a shell string, so a path or a URL is never parsed again.
* A timeout that kills *and reaps* the child, so a hung simulator service holds no request open and leaves no zombie.
* A missing Xcode is a result, not an exception.
* stdin closed unless something is written to it (``simctl pbcopy`` reads its text there), so a tool that unexpectedly
  wants input sees end-of-file.

``developer_dir`` is the ``device.developer_dir`` setting. When set it becomes ``DEVELOPER_DIR`` for the child, so a
scope can use one of several installed Xcodes without touching ``xcode-select``, which is the whole machine's.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.platform.process import kill_and_reap, spawn

#: ``rc`` when there is no xcrun: no Xcode and no command-line tools.
XCRUN_MISSING = 127
#: ``rc`` when xcrun could not be started.
CANNOT_RUN = 126
#: ``rc`` when the call did not finish in time and was killed.
TIMED_OUT = 124

#: Where the command-line tools always put it.
SYSTEM_XCRUN = "/usr/bin/xcrun"


@dataclass(frozen=True)
class XcrunResult:
    """What one xcrun call did. ``out`` and ``err`` are decoded text; ``raw`` is stdout as written."""

    rc: int
    out: str
    err: str
    raw: bytes = field(default=b"", compare=False, repr=False)

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def message(self) -> str:
        """The most useful line to show a person: the last non-empty line of stderr, else of stdout."""
        for text in (self.err, self.out):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                return lines[-1]
        return f"xcrun exited with {self.rc}"


class XcrunRunner(Protocol):
    """How SimMirror runs xcrun; tests pass a fake with this shape."""

    async def __call__(
        self,
        *args: str,
        timeout: float = ...,
        developer_dir: str = ...,
        input_data: bytes | None = ...,
        cwd: Path | str | None = ...,
    ) -> XcrunResult:
        """Run ``xcrun <args>`` and report what happened, as `run_xcrun` does."""
        ...


def xcrun_binary() -> str | None:
    """The xcrun to run, or None when this machine has none."""
    if os.path.isfile(SYSTEM_XCRUN) and os.access(SYSTEM_XCRUN, os.X_OK):
        return SYSTEM_XCRUN
    return shutil.which("xcrun")


def xcrun_env(developer_dir: str = "") -> dict[str, str]:
    """The environment an xcrun child runs with: this process's, pointed at the chosen Xcode."""
    env = dict(os.environ)
    if developer_dir:
        env["DEVELOPER_DIR"] = developer_dir
    return env


async def run_xcrun(
    *args: str,
    timeout: float = 30.0,
    developer_dir: str = "",
    input_data: bytes | None = None,
    cwd: Path | str | None = None,
) -> XcrunResult:
    """Run ``xcrun <args>`` and report what happened. Never raises."""
    xcrun = xcrun_binary()
    if xcrun is None:
        return XcrunResult(XCRUN_MISSING, "", "Xcode command-line tools are not installed (no xcrun)")
    try:
        proc = await asyncio.create_subprocess_exec(
            xcrun,
            *args,
            cwd=None if cwd is None else str(cwd),
            stdin=asyncio.subprocess.DEVNULL if input_data is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=xcrun_env(developer_dir),
        )
    except OSError as exc:
        return XcrunResult(CANNOT_RUN, "", f"cannot run xcrun: {exc}")
    talk = proc.communicate() if input_data is None else proc.communicate(input_data)
    try:
        out, err = await asyncio.wait_for(talk, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        await kill_and_reap(proc)
        return XcrunResult(TIMED_OUT, "", f"xcrun {' '.join(args[:2])} did not finish within {timeout:g} seconds")
    except asyncio.CancelledError:
        # The caller went away -- a device ended while it boots, say: the child does not outlive the call.
        await kill_and_reap(proc)
        raise
    raw = out or b""
    return XcrunResult(proc.returncode or 0, raw.decode(errors="replace"), (err or b"").decode(errors="replace"), raw)


async def start_xcrun(*args: str, log_path: Path, developer_dir: str = "", cwd: Path | None = None) -> Any:
    """Start a long ``xcrun <args>`` -- a build -- in a process group of its own, its output appended to `log_path`.

    Answers the process, to be waited for and ended by its group (`process`). A missing Xcode raises
    `FileNotFoundError`, as a missing program would.
    """
    xcrun = xcrun_binary()
    if xcrun is None:
        raise FileNotFoundError("Xcode command-line tools are not installed (no xcrun)")
    return await spawn([xcrun, *args], log_path, cwd=cwd, env=xcrun_env(developer_dir))
