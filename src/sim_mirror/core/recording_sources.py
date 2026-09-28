# SPDX-License-Identifier: Apache-2.0
"""Where a recording's pictures come from: the device's own tool, or the screen SimMirror already streams.

* `ToolRecording` -- ``simctl io recordVideo`` for a simulator: a movie at the device's own resolution, written by a
  process that runs until it is sent SIGINT, which finalizes the file. The first frame is taken once it says so.
* `StreamRecording` -- for a device whose tool cannot record, a real iPhone: every frame the device's `FrameHub`
  streams -- H.264 access units or JPEG screenshots -- written with the time it came, into SimMirror's own frame file,
  which the native helper turns into a movie (`frame_file`).

Either way the recording knows when its first frame was taken, so the touches drawn onto it land where they did.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import struct
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import IO, Any, Literal, Protocol

from sim_mirror.core.frames import FrameHub
from sim_mirror.platform import process
from sim_mirror.platform.errors import DeviceControlError

#: How long a device's tool may take to take its first frame.
START_TIMEOUT_S = 10.0
#: How long a device's tool may take to finalize its movie once asked to stop.
STOP_TIMEOUT_S = 30.0
POLL_S = 0.05

#: SimMirror's frame file: this, then per frame its kind, its time in seconds since the first, its length, its bytes.
FRAME_FILE_MAGIC = b"SMRF\x01"
FRAME_HEADER = struct.Struct(">BdI")
FRAME_KINDS = {"h264": 1, "jpeg": 2}

SourceKind = Literal["movie", "frames"]


class RecordingSource(Protocol):
    """Pictures of a device's screen being recorded into `raw`."""

    #: What `raw` holds once stopped: a movie, or SimMirror's frame file.
    kind: SourceKind
    raw: Path

    async def start(self) -> float:
        """Begin, answering the clock's time when the first frame was taken."""
        ...

    async def stop(self) -> None:
        """Finish: `raw` is whole once this returns. Stopping twice is stopping once."""
        ...


class ToolRecording:
    """A recording made by the device's own tool, as a process of its own."""

    kind: SourceKind = "movie"

    def __init__(
        self,
        begin: Callable[[Path, Path], Awaitable[Any]],
        raw: Path,
        log: Path,
        *,
        started: str,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        start_timeout: float = START_TIMEOUT_S,
        stop_timeout: float = STOP_TIMEOUT_S,
        signal_group: Callable[[int, int], None] = process.signal_group,
        kill: Callable[[Any], Awaitable[None]] = process.kill_and_reap,
    ) -> None:
        self.raw = raw
        self._begin = begin
        self._log = log
        self._started = started
        self._clock = clock
        self._sleep = sleep
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._signal_group = signal_group
        self._kill = kill
        self._proc: Any = None

    def _said(self) -> str:
        try:
            return self._log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    async def start(self) -> float:
        self._proc = await self._begin(self.raw, self._log)
        waited = 0.0
        while self._started not in self._said():
            if self._proc.returncode is not None:
                raise DeviceControlError(f"the recording did not start: {self._said().strip()[-300:] or 'no reason'}")
            if waited >= self._start_timeout:
                await self._kill(self._proc)
                raise DeviceControlError(f"the recording did not start within {self._start_timeout:g}s")
            await self._sleep(POLL_S)
            waited += POLL_S
        return self._clock()

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            self._signal_group(proc.pid, signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), self._stop_timeout)
            except TimeoutError:
                await self._kill(proc)
        if not self.raw.is_file() or self.raw.stat().st_size == 0:
            raise DeviceControlError(f"the recording wrote no movie: {self._said().strip()[-300:] or 'no reason'}")


class StreamRecording:
    """A recording of the frames a device's `FrameHub` streams, each written with its time."""

    kind: SourceKind = "frames"

    def __init__(self, hub: FrameHub, raw: Path, *, encoding: str, clock: Callable[[], float] = time.monotonic) -> None:
        if encoding not in FRAME_KINDS:
            raise ValueError(f"not a stream kind: {encoding!r}")
        self.raw = raw
        self._hub = hub
        self._encoding = encoding
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._file: IO[bytes] | None = None
        self.frames = 0

    async def start(self) -> float:
        began = self._clock()
        self._file = open(self.raw, "wb")  # noqa: SIM115 -- open until stop() closes it
        self._file.write(FRAME_FILE_MAGIC)
        subscriber = self._hub.subscribe(self._encoding)
        tag = FRAME_KINDS[self._encoding]
        file = self._file

        async def record() -> None:
            try:
                while (frame := await subscriber.next()) is not None:
                    file.write(FRAME_HEADER.pack(tag, self._clock() - began, len(frame.data)))
                    file.write(frame.data)
                    self.frames += 1
            finally:
                self._hub.unsubscribe(subscriber)

        self._task = asyncio.get_running_loop().create_task(record())
        return began

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._file is not None:
            self._file.close()
            self._file = None
        if self.frames == 0:
            raise DeviceControlError("the recording took no frames: the screen sent none while it ran")
