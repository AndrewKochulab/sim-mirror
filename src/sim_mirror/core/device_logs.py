# SPDX-License-Identifier: Apache-2.0
"""A real device's log, kept while SimMirror drives it by cable, for ``sim_app logs``.

A simulator's log is on the Mac and asked for when wanted; a real device's is streamed by its ``syslog_relay`` service
(`platform.lockdown`) and gone once sent, so SimMirror reads it from the moment it attaches a cabled device, on a thread
of its own, into a buffer of ``real_devices.log_buffer_mb`` -- a busy device writes a megabyte in seconds, so the
oldest lines go first. It is read as a simulator's is: an app's lines, by its process, or the errors and faults.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)

#: A syslog line: month, day, time, host, then the process, its subsystem in brackets, its pid and its level.
_LINE = re.compile(r"\A\w{3}\s+\d+\s+[\d:.]+\s+\S+\s+(?P<process>[^\s(\[]+)")
_LEVELS = ("<Error>:", "<Fault>:")
#: How much is read from the device's log at a time.
CHUNK = 65536


class LogStream(Protocol):
    """Where a device's log arrives: its relay service's socket."""

    def recv(self, size: int) -> bytes: ...

    def close(self) -> None: ...


def process_of(line: str) -> str | None:
    """The process a syslog line is from, or None when it is not a syslog line."""
    match = _LINE.match(line)
    return match.group("process") if match else None


def wanted(line: str, bundle_id: str | None) -> bool:
    """Whether a line is an app's -- its process named as the bundle's last part, or the bundle named in it -- or,
    with no app named, an error or a fault."""
    if bundle_id is None:
        return any(level in line for level in _LEVELS)
    process = process_of(line)
    return (process is not None and process.lower() == bundle_id.rsplit(".", 1)[-1].lower()) or bundle_id in line


class LogBuffer:
    """One device's latest log lines, as many as fit in a number of bytes, each with when it came."""

    def __init__(self, max_bytes: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_bytes = max_bytes
        self._clock = clock
        self._lines: deque[tuple[float, str]] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        with self._lock:
            self._lines.append((self._clock(), line))
            self._size += len(line)
            while self._size > self._max_bytes and self._lines:
                self._size -= len(self._lines.popleft()[1])

    def lines(self, since_s: float, bundle_id: str | None) -> list[str]:
        """The lines of the last `since_s` seconds that are an app's, or the errors and faults, oldest first."""
        since = self._clock() - since_s
        with self._lock:
            kept = list(self._lines)
        return [line for when, line in kept if when >= since and wanted(line, bundle_id)]


class Reader:
    """Reads one device's log stream into its buffer on a thread of its own, until it is stopped or the stream ends."""

    def __init__(self, stream: LogStream, buffer: LogBuffer) -> None:
        self._stream = stream
        self.buffer = buffer
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sim-mirror-device-log", daemon=True)

    def start(self) -> Reader:
        self._thread.start()
        return self

    def _run(self) -> None:
        carried = b""
        while not self._stopped.is_set():
            try:
                chunk = self._stream.recv(CHUNK)
            except OSError:
                break
            if not chunk:
                break
            *whole, carried = (carried + chunk).replace(b"\x00", b"\n").split(b"\n")
            for raw in whole:
                if raw.strip():
                    self.buffer.add(raw.decode("utf-8", errors="replace"))
        self._stopped.set()

    @property
    def alive(self) -> bool:
        return not self._stopped.is_set()

    def stop(self) -> None:
        self._stopped.set()
        # A socket that will not close is already closed.
        with contextlib.suppress(OSError):
            self._stream.close()
        self._thread.join(timeout=2)


class DeviceLogBook:
    """Every attached real device's log: begun when its connector reaches it by cable, ended when it is let go."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._readers: dict[str, Reader] = {}

    def start(self, udid: str, stream: LogStream, *, max_bytes: int) -> None:
        """Keep this device's log from `stream`, in a buffer of `max_bytes`, in place of whatever was kept before."""
        self.stop(udid)
        self._readers[udid] = Reader(stream, LogBuffer(max_bytes, clock=self._clock)).start()
        logger.info("keeping the log of %s, up to %d bytes", udid, max_bytes)

    def stop(self, udid: str) -> None:
        reader = self._readers.pop(udid, None)
        if reader is not None:
            reader.stop()

    def lines(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str] | None:
        reader = self._readers.get(udid)
        return None if reader is None else reader.buffer.lines(since_s, bundle_id)
