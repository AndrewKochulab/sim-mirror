# SPDX-License-Identifier: Apache-2.0
"""Which process on the Mac is using which device.

Two SimMirrors -- the standalone daemon and an application embedding SimMirror, or two copies of either -- must not
drive one device at once: each would start its own companion for it, and each would stop the other's. So before a
process brings a device up it claims it, in a file named by the device in a folder every host shares
(`app_support.claims_dir`), recording who it is: an owner name, its pid, and when that process started.

A claim holds while its process is alive -- the same pid, started at the same time, so a pid the system reused for
something else does not hold a device forever. A dead process's claim is simply taken over. The files are changed
under a lock (`private.file_lock`), and no lock is held across an await: whether a claim's process lives is found
first, then checked again under the lock against the file as it is.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from sim_mirror.platform import process
from sim_mirror.storage.private import file_lock, write_private

_DEVICE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
LOCK_FILE = ".lock"
ATTEMPTS = 3


@dataclass(frozen=True)
class Claim:
    udid: str
    owner: str
    pid: int
    #: When the claiming process started, as ``ps`` says it; None when that could not be read.
    started: str | None
    label: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> Claim | None:
        try:
            raw = json.loads(data)
            claim = cls(
                udid=str(raw["udid"]),
                owner=str(raw["owner"]),
                pid=int(raw["pid"]),
                started=None if raw.get("started") is None else str(raw["started"]),
                label=str(raw.get("label", "")),
            )
        except (ValueError, TypeError, KeyError):
            return None
        return claim


class DeviceClaimed(Exception):
    """A device another live process is using."""

    def __init__(self, claim: Claim) -> None:
        super().__init__(f"{claim.owner} (pid {claim.pid}) on this Mac is already using this simulator")
        self.claim = claim


class Claims:
    """This process's claims, in a folder every host on the Mac shares."""

    def __init__(
        self,
        folder: Path,
        *,
        owner: str,
        label: str = "",
        pid: int | None = None,
        pid_alive: Callable[[int], bool] = process.pid_alive,
        start_time: Callable[[int], Awaitable[str | None]] = process.start_time,
    ) -> None:
        self._folder = folder
        self._owner = owner
        self._label = label
        self._pid = os.getpid() if pid is None else pid
        self._pid_alive = pid_alive
        self._start_time = start_time
        self._started: str | None = None
        self._started_known = False

    def _path(self, udid: str) -> Path:
        if not _DEVICE_ID.match(udid):
            raise ValueError(f"not a device id: {udid!r}")
        return self._folder / f"{udid}.json"

    def _read(self, udid: str) -> Claim | None:
        try:
            data = self._path(udid).read_bytes()
        except OSError:
            return None
        return Claim.from_json(data)

    async def _alive(self, claim: Claim) -> bool:
        if claim.pid <= 1 or not self._pid_alive(claim.pid):
            return False
        if claim.started is None:
            return True
        now = await self._start_time(claim.pid)
        return now is None or now == claim.started

    async def _mine(self, udid: str) -> Claim:
        if not self._started_known:
            self._started = await self._start_time(self._pid)
            self._started_known = True
        return Claim(udid=udid, owner=self._owner, pid=self._pid, started=self._started, label=self._label)

    async def holder(self, udid: str) -> Claim | None:
        """The live claim another process has on a device, or None."""
        claim = self._read(udid)
        if claim is None or claim.pid == self._pid:
            return None
        return claim if await self._alive(claim) else None

    async def acquire(self, udid: str) -> None:
        """Claim a device for this process. Raises `DeviceClaimed` while another live process has it."""
        mine = await self._mine(udid)
        for _attempt in range(ATTEMPTS):
            seen = self._read(udid)
            taken = seen is not None and seen.pid != self._pid and await self._alive(seen)
            with file_lock(self._folder / LOCK_FILE):
                if self._read(udid) != seen:
                    continue
                if taken and seen is not None:
                    raise DeviceClaimed(seen)
                write_private(self._path(udid), mine.to_json())
                return
        raise DeviceClaimed(self._read(udid) or mine)

    async def release(self, udid: str) -> bool:
        """Let go of a device this process claimed. Answers whether it had."""
        path = self._path(udid)
        with file_lock(self._folder / LOCK_FILE):
            claim = self._read(udid)
            if claim is None or claim.pid != self._pid:
                return False
            path.unlink(missing_ok=True)
            return True
