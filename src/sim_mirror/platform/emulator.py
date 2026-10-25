# SPDX-License-Identifier: Apache-2.0
"""The Android emulator: started for an AVD, known again after SimMirror restarts, and spoken to through its console.

**Starting** runs ``emulator -avd <name> -port <port>`` in a process group of its own, with its output in SimMirror's
log folder -- headless (``-no-window``) unless ``android.emulator.window`` shows its own window, with no audio and no
boot animation. The port is chosen from the even ones the emulator takes (5554-5680) that no running emulator has, and
names the device to adb: ``emulator-5554``.

**Knowing which ones SimMirror started**: each start leaves a pid file under SimMirror's run folder naming the pid,
the host that started it (its owner tag), the AVD and the port. An emulator SimMirror started is shut down as a
simulator it booted is; one a person started -- from Android Studio, say -- is not, and a restart of SimMirror tells the
two apart by these files (`Emulators.started_here`). A pid file whose pid no longer runs the emulator is stale and is
thrown away; one another host wrote is never read as this one's.

**The console** listens on 127.0.0.1 at the emulator's port. It asks for the token in
``~/.emulator_console_auth_token`` (read here only, never logged or put in a command line), then answers each command
with its lines and ``OK``, or ``KO: <why>``. SimMirror asks it for ``geo fix`` -- where the device is -- and ``kill``,
which ends the emulator as its own window's close button does, saving its quick-boot snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.platform import process
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.storage.private import ensure_private_dir

#: The console ports the emulator takes: even numbers, the next one up its adb port.
PORTS = range(5554, 5682, 2)
GPUS = ("auto", "host", "swiftshader_indirect")
TOKEN_FILE = Path("~/.emulator_console_auth_token")
#: How long the console may take to answer a command.
CONSOLE_TIMEOUT_S = 10.0
#: The longest answer the console is trusted to send.
CONSOLE_LARGEST = 64 * 1024
TOO_MUCH = "the emulator's console answered more than a console answer holds"
#: The serial adb gives an emulator, by its console port.
SERIAL_PREFIX = "emulator-"


class EmulatorError(DeviceControlError):
    """The emulator could not be started, or its console refused what it was asked."""


@dataclass(frozen=True)
class EmulatorOptions:
    """How an emulator is started: with its own window or headless, which graphics, and whether from cold."""

    window: bool = False
    gpu: str = "auto"
    cold_boot: bool = False


def argv(emulator: Path, avd: str, port: int, options: EmulatorOptions) -> list[str]:
    """The command that starts an AVD on a console port."""
    return [
        str(emulator),
        "-avd",
        avd,
        "-port",
        str(port),
        *(() if options.window else ("-no-window",)),
        "-no-audio",
        "-no-boot-anim",
        "-gpu",
        options.gpu if options.gpu in GPUS else "auto",
        *(("-no-snapshot-load",) if options.cold_boot else ()),
    ]


def port_of(serial: str) -> int | None:
    """An emulator's console port, from its adb serial; None for anything else."""
    rest = serial[len(SERIAL_PREFIX) :] if serial.startswith(SERIAL_PREFIX) else ""
    return int(rest) if rest.isdigit() else None


def free_port(used: Collection[int]) -> int:
    """A console port no running emulator has."""
    for port in PORTS:
        if port not in used and port + 1 not in used:
            return port
    raise EmulatorError(f"every emulator port ({PORTS.start}-{PORTS.stop - 2}) is taken: shut an emulator down")


@dataclass(frozen=True)
class Started:
    """An emulator SimMirror started: its pid, its AVD and its console port."""

    pid: int
    avd: str
    port: int

    @property
    def serial(self) -> str:
        return f"{SERIAL_PREFIX}{self.port}"


class Emulators:
    """The emulators this host started, by their pid files."""

    def __init__(
        self,
        folder: Path,
        log_dir: Path,
        owner_tag: str,
        *,
        spawn: Callable[..., Awaitable[Any]] = process.spawn,
        alive: Callable[[int], bool] = process.pid_alive,
        command_of: Callable[[int], Awaitable[str | None]] = process.command_of,
    ) -> None:
        self._folder = folder
        self._log_dir = log_dir
        self._owner = owner_tag
        self._spawn = spawn
        self._alive = alive
        self._command_of = command_of

    def _pid_file(self, avd: str) -> Path:
        return self._folder / f"{avd}.json"

    async def start(self, emulator: Path, avd: str, port: int, options: EmulatorOptions) -> Started:
        """Start an AVD on a console port, and leave word that this host started it."""
        if not emulator.is_file():
            raise EmulatorError(f"the Android emulator is not installed at {emulator}: install it with the SDK")
        try:
            proc = await self._spawn(argv(emulator, avd, port, options), self._log_dir / f"emulator-{avd}.log")
        except OSError as exc:
            raise EmulatorError(f"the Android emulator could not be started: {exc}") from exc
        started = Started(proc.pid, avd, port)
        folder = ensure_private_dir(self._folder)
        record = {"pid": started.pid, "owner": self._owner, "avd": avd, "port": port}
        (folder / self._pid_file(avd).name).write_text(json.dumps(record), encoding="utf-8")
        return started

    async def started_here(self, avd: str) -> Started | None:
        """The emulator this host started for an AVD, while it still runs; None for one it did not start."""
        try:
            record = json.loads(self._pid_file(avd).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("owner") != self._owner:
            return None
        pid, port = record.get("pid"), record.get("port")
        if not isinstance(pid, int) or not isinstance(port, int) or not self._alive(pid):
            self.forget(avd)
            return None
        command = await self._command_of(pid)
        if command is None or "emulator" not in command or avd not in command:
            self.forget(avd)
            return None
        return Started(pid, avd, port)

    def forget(self, avd: str) -> None:
        """Let go of an emulator's pid file: it was shut down, or it is no longer the one that was started."""
        with contextlib.suppress(OSError):
            self._pid_file(avd).unlink()


Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def _open(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


def read_token(path: Path = TOKEN_FILE) -> str:
    """The console's auth token; "" where the emulator has written none, as one started with ``-no-auth`` has."""
    with contextlib.suppress(OSError):
        return path.expanduser().read_text(encoding="utf-8").strip()
    return ""


class EmulatorConsole:
    """An emulator's console: one connection per command, authenticated each time."""

    def __init__(
        self,
        port: int,
        *,
        token: Callable[[], str] = read_token,
        opener: Opener = _open,
        timeout: float = CONSOLE_TIMEOUT_S,
    ) -> None:
        self.port = port
        self._token = token
        self._open = opener
        self._timeout = timeout

    async def command(self, line: str) -> str:
        """A command's answer, without its ``OK`` -- or `EmulatorError` with what the console said."""
        if "\n" in line or "\r" in line:
            raise EmulatorError("a console command is one line")
        try:
            return await asyncio.wait_for(self._command(line), self._timeout)
        except (OSError, asyncio.TimeoutError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise EmulatorError(f"the emulator's console on port {self.port} does not answer") from exc
        except ValueError as exc:
            # A line longer than a stream reads at once.
            raise EmulatorError(TOO_MUCH) from exc

    async def _command(self, line: str) -> str:
        reader, writer = await self._open("127.0.0.1", self.port)
        try:
            await self._answer(reader)
            token = self._token()
            if token:
                writer.write(f"auth {token}\n".encode())
                await self._answer(reader)
            writer.write(f"{line}\n".encode())
            return await self._answer(reader)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _answer(reader: asyncio.StreamReader) -> str:
        lines: list[str] = []
        size = 0
        while True:
            raw = await reader.readline()
            if not raw:
                raise asyncio.IncompleteReadError(b"", None)
            size += len(raw)
            if size > CONSOLE_LARGEST:
                raise EmulatorError(TOO_MUCH)
            text = raw.decode(errors="replace").rstrip("\r\n")
            if text == "OK":
                return "\n".join(lines)
            if text.startswith("KO"):
                raise EmulatorError(f"the emulator's console refused it: {text[2:].lstrip(': ') or 'no reason'}")
            lines.append(text)

    async def avd_name(self) -> str:
        return (await self.command("avd name")).strip()

    async def geo_fix(self, latitude: float, longitude: float) -> None:
        """Put the device at a place: the console takes longitude first."""
        await self.command(f"geo fix {longitude:.6f} {latitude:.6f}")

    async def kill(self) -> None:
        """End the emulator, as closing its window does: its quick-boot snapshot is saved."""
        with contextlib.suppress(EmulatorError):
            await self.command("kill")
