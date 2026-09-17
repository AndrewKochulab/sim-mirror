# SPDX-License-Identifier: Apache-2.0
"""sim-mirror-helper: found, asked its version, started and ended only here -- one per booted device.

The native helper is SimMirror's own Swift program (``helper/``) that reaches a simulator's screen, input and element
tree through Apple's frameworks. It lives the life every helper does (`connectors.helper_process`): its own process
group, the scope's Xcode, a unix socket and pid file in the host's run folder, ready when it answers, and a helper a
crashed host left running ended the next time that host starts. What is the native helper's own is here:

* **found** where it is (`find_helper`): the configured path, else the copy shipped inside SimMirror's wheel, else the
  copy `sim-mirror helper build` built for this version;
* **asked its version** (`helper_version`) -- a helper of another version or wire protocol is not used -- and, by the
  doctor, **to check a device** (`self_check`): reach its screen, a picture, input and the element tree, and say which
  worked;
* **run with its flags** (`helper_argv`), told the process that started it so it ends when that process does;
* **kept in a folder of its own** under the run folder, so idb_companion's cleanup never touches its files.

`scripts/check_containment.py` keeps every other module from starting one.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sim_mirror._version import __version__
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.connectors.helper_process import HelperProcesses, HelperSpec, RunningHelper, Spawn
from sim_mirror.connectors.native import wire
from sim_mirror.connectors.native.client import HelperClient
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform import process
from sim_mirror.platform.process import Runner
from sim_mirror.storage.private import ensure_private_dir

PROGRAM = "sim-mirror-helper"
#: How long a self-check may take: the first read of a device's element tree waits for its accessibility to wake up.
SELF_CHECK_TIMEOUT_S = 60.0
#: The folder under the run folder the helpers' sockets and pid files are kept in.
FOLDER = "native"
#: Where the wheel ships the helper, beside this package.
PACKAGED = Path(__file__).resolve().parents[2] / "_bin" / PROGRAM
_HERE = Path(__file__).resolve()
#: Where the helper's Swift package is: inside the wheel, or at the top of a source checkout.
SOURCE_CANDIDATES = (_HERE.parents[2] / "_helper_src", _HERE.parents[4] / "helper")


class HelperUnavailable(ConnectorUnavailable):
    """A native helper that could not be had, with the HTTP status it means."""


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def built_helper(state_dir: Path, version: str = __version__) -> Path:
    """Where `sim-mirror helper build` puts the helper for a version of SimMirror."""
    return state_dir / "helpers" / f"native-{version}" / PROGRAM


def helper_sources(candidates: Sequence[Path] = SOURCE_CANDIDATES) -> Path | None:
    """The helper's Swift package this install has, or None."""
    return next((folder for folder in candidates if (folder / "Package.swift").is_file()), None)


def find_helper(configured: str, candidates: Sequence[Path]) -> str | None:
    """The helper to run: the configured one when it runs, else the first candidate installed.

    A configured path that cannot be run is not quietly replaced by another copy: the person named that one.
    """
    if configured:
        return configured if _runnable(Path(configured)) else None
    return next((str(path) for path in candidates if _runnable(path)), None)


@dataclass(frozen=True)
class HelperVersion:
    """What a helper says of itself."""

    version: str
    wire: int
    core_simulator: str | None

    @property
    def usable(self) -> bool:
        """Whether this SimMirror can use it: the same version, and the same wire protocol."""
        return self.version == __version__ and self.wire == wire.VERSION


@dataclass(frozen=True)
class FoundHelper:
    """The helper an install would run, if any, and what it says of its version."""

    binary: str | None
    version: HelperVersion | None = None

    @property
    def usable(self) -> bool:
        return self.version is not None and self.version.usable

    def reason(self, copy: HostCopy, configured: str) -> str | None:
        """Why it cannot be used, said so a person can act on it; None when it can."""
        if self.binary is None:
            return copy.helper_missing(configured)
        wanted = f"version {__version__} (wire {wire.VERSION})"
        if self.version is None:
            return copy.helper_mismatch(self.binary, "a helper that does not say its version", wanted)
        if not self.version.usable:
            found = f"version {self.version.version} (wire {self.version.wire})"
            return copy.helper_mismatch(self.binary, found, wanted)
        return None


async def locate_helper(
    configured: str, candidates: Sequence[Path], ask_version: Callable[[str], Awaitable[HelperVersion | None]]
) -> FoundHelper:
    """The helper to run (`find_helper`) and its version."""
    binary = find_helper(configured, candidates)
    return FoundHelper(binary, await ask_version(binary) if binary else None)


async def helper_version(binary: str, run: Runner = process.run) -> HelperVersion | None:
    """What a helper prints for ``version``, or None when it cannot be run or says nothing readable."""
    code, out = await run((binary, "version"))
    if code != 0:
        return None
    try:
        data: object = json.loads(out)
        if not isinstance(data, dict):
            return None
        core = data.get("core_simulator")
        return HelperVersion(str(data["version"]), int(data["wire"]), str(core) if core else None)
    except (ValueError, KeyError, TypeError):
        return None


@dataclass(frozen=True)
class SelfCheckPart:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class SelfCheck:
    """What a helper found when it checked a device, part by part."""

    parts: tuple[SelfCheckPart, ...]

    @property
    def ok(self) -> bool:
        return all(part.ok for part in self.parts)


async def _run_self_check(argv: Sequence[str]) -> tuple[int, str]:
    return await process.run(argv, timeout=SELF_CHECK_TIMEOUT_S)


async def self_check(
    binary: str,
    udid: str,
    developer_dir: str = "",
    run: Callable[[Sequence[str]], Awaitable[tuple[int, str]]] = _run_self_check,
) -> SelfCheck | None:
    """What a helper finds when it reaches a device with the Xcode at `developer_dir`, or None when it says nothing
    readable. It exits 1 when a part did not work, and says which, so its output is read either way."""
    command = (binary, "self-check", "--udid", udid)
    argv = ("/usr/bin/env", f"DEVELOPER_DIR={developer_dir}", *command) if developer_dir else command
    _code, out = await run(argv)
    try:
        data: object = json.loads(out)
        if not isinstance(data, dict):
            return None
        parts = tuple(SelfCheckPart(str(part["name"]), bool(part["ok"]), str(part["detail"])) for part in data["parts"])
    except (ValueError, KeyError, TypeError):
        return None
    return SelfCheck(parts)


def helper_argv(
    binary: str, udid: str, socket: Path, *, parent: int, hid: str, idle_key_frames: bool
) -> tuple[str, ...]:
    return (
        binary, "serve", "--udid", udid, "--socket", str(socket), "--parent-pid", str(parent), "--hid", hid,
        "--idle-key-frames", "on" if idle_key_frames else "off",
    )  # fmt: skip


RunningNative = RunningHelper[HelperClient]


class HelperLauncher(HelperProcesses[HelperClient]):
    """Starts, ends and cleans up after native helpers. The keyword arguments after `copy` are the test seams."""

    def __init__(
        self,
        *,
        run_dir: Path,
        log_dir: Path,
        owner_tag: str,
        copy: HostCopy | None = None,
        spawn: Spawn = process.spawn,
        connect: Callable[[str], HelperClient] = HelperClient.at,
        signal_group: Callable[[int, int], None] = process.signal_group,
        pid_alive: Callable[[int], bool] = process.pid_alive,
        command_of: Callable[[int], Awaitable[str | None]] = process.command_of,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner: int | None = None,
        ensure_dir: Callable[[Path], Path] = ensure_private_dir,
    ) -> None:
        super().__init__(
            HelperSpec(program=PROGRAM, log_prefix=FOLDER, unavailable=HelperUnavailable),
            folder=run_dir / FOLDER,
            log_dir=log_dir,
            owner_tag=owner_tag,
            connect=connect,
            copy=copy,
            spawn=spawn,
            signal_group=signal_group,
            pid_alive=pid_alive,
            command_of=command_of,
            clock=clock,
            sleep=sleep,
            owner=owner,
            ensure_dir=ensure_dir,
        )

    async def start(
        self,
        binary: str,
        udid: str,
        developer_dir: str = "",
        *,
        hid: str = "auto",
        idle_key_frames: bool = True,
        ready_timeout_s: float | None = None,
    ) -> RunningNative:
        """A helper for this booted device, running with the Xcode at `developer_dir` and ready to answer."""

        def argv(socket: Path) -> tuple[str, ...]:
            return helper_argv(binary, udid, socket, parent=self._owner, hid=hid, idle_key_frames=idle_key_frames)

        return await self.launch(argv, udid, developer_dir, ready_timeout_s=ready_timeout_s)
