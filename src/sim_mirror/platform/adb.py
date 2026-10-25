# SPDX-License-Identifier: Apache-2.0
"""The adb server, spoken to directly -- the way adb itself talks to it, with no adb process per command.

adb's server runs on this Mac (127.0.0.1:5037) and reaches every emulator and every phone adb knows. A request is a
four-digit hex length and a service name; the server answers ``OKAY``, or ``FAIL`` and why. A service on a device is
two requests on one connection: ``host:transport:<serial>``, then the service, after which the connection *is* that
service -- a shell's output, a file being pushed, a socket on the device:

* ``shell,v2,raw:<command>`` runs a command and says how it exited: each packet is a kind (1 stdout, 2 stderr, 3 its
  exit code), a little-endian length, and that many bytes. Measured on an emulator, a warm call takes about 7 ms.
* ``exec:<command>`` streams a command's raw output -- a screenshot, a UI dump -- with nothing in its way.
* ``sync:`` pushes and reads files (``SEND``, ``DATA``, ``DONE``; ``STAT``).
* ``localabstract:<name>`` opens a unix socket a program on the device listens on: scrcpy's server is reached this way,
  so nothing listens on this Mac -- no ``adb forward``, no TCP port.

The server is started with ``adb start-server`` only when SimMirror is asked to reach a device and nothing answers
(`ensure_server`), never by a check that only looks. `ADB_SERVER_ENV` moves the address -- ``host:port``, or the path of
a unix socket -- so tests never reach the real server.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import struct
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sim_mirror.platform import process
from sim_mirror.platform.errors import DeviceControlError

#: Moves the adb server's address: ``host:port``, or a unix socket's path.
ADB_SERVER_ENV = "SIM_MIRROR_ADB_SERVER"
#: adb's own variable for its server's port.
ADB_PORT_ENV = "ANDROID_ADB_SERVER_PORT"
DEFAULT_PORT = 5037
#: How long a request may take, unless the call says otherwise.
TIMEOUT_S = 10.0
#: How long ``adb start-server`` may take.
START_TIMEOUT_S = 15.0
#: The largest piece of a file one ``DATA`` packet carries.
SYNC_CHUNK = 64 * 1024

_SHELL_PACKET = struct.Struct("<BI")
_STDOUT, _STDERR, _EXIT = 1, 2, 3
_SYNC = struct.Struct("<4sI")

Connection = Literal["emulator", "usb", "network"]


async def start_server(argv: Sequence[str]) -> tuple[int, str]:
    """Run ``adb start-server``, which starts the server in the background and returns once it answers."""
    return await process.run(argv, timeout=START_TIMEOUT_S)


class AdbError(DeviceControlError):
    """The adb server could not be reached, or refused what it was asked, or a command on a device failed."""


@dataclass(frozen=True)
class AdbDevice:
    """A device adb lists: its serial, how it stands (``device``, ``offline``, ``unauthorized``…), and what it says."""

    serial: str
    state: str
    properties: Mapping[str, str] = field(default_factory=dict)

    @property
    def connection(self) -> Connection:
        """How the device reaches this Mac: an emulator on it, a phone by its cable, or a phone on the network."""
        if self.serial.startswith("emulator-"):
            return "emulator"
        if ":" in self.serial or "._adb-tls-connect." in self.serial:
            return "network"
        return "usb"

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def model(self) -> str:
        return self.properties.get("model", "").replace("_", " ")


@dataclass(frozen=True)
class ShellResult:
    code: int
    out: bytes
    err: bytes

    @property
    def text(self) -> str:
        return self.out.decode(errors="replace")

    @property
    def message(self) -> str:
        """What a failed command said, for a refusal."""
        said = (self.err or self.out).decode(errors="replace").strip()
        return said.splitlines()[-1] if said else f"it exited with {self.code}"


@dataclass(frozen=True)
class FileStat:
    mode: int
    size: int
    mtime: int

    @property
    def exists(self) -> bool:
        return self.mode != 0


def parse_devices(listing: str) -> list[AdbDevice]:
    """``host:devices-l``'s answer: a line a device, its serial and state, then ``key:value`` words."""
    found = []
    for line in listing.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        properties = dict(word.split(":", 1) for word in words[2:] if ":" in word)
        found.append(AdbDevice(words[0], words[1], properties))
    return found


def server_address(env: Mapping[str, str] = os.environ) -> str:
    """Where the adb server listens: ``host:port``, or a unix socket's path."""
    moved = env.get(ADB_SERVER_ENV, "")
    if moved:
        return moved
    port = env.get(ADB_PORT_ENV, "")
    return f"127.0.0.1:{port if port.isdigit() else DEFAULT_PORT}"


Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Opener = Callable[[str], Awaitable[Streams]]


async def open_address(address: str) -> Streams:
    """A connection to an address as `server_address` says it."""
    if "/" in address:
        return await asyncio.open_unix_connection(address)
    host, _, port = address.rpartition(":")
    return await asyncio.open_connection(host or "127.0.0.1", int(port))


def command_line(command: str | Sequence[str]) -> str:
    """A command for the device's shell: an argv is quoted word by word, so nothing in it is read as shell."""
    return command if isinstance(command, str) else shlex.join(command)


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


class Adb:
    """The adb server, one connection per request as it expects."""

    def __init__(
        self,
        address: str | None = None,
        *,
        timeout: float = TIMEOUT_S,
        opener: Opener = open_address,
        adb_path: Callable[[], Path | None] = lambda: None,
        starter: process.Runner = start_server,
    ) -> None:
        self.address = address or server_address()
        self._timeout = timeout
        self._open = opener
        #: Where adb is, for starting its server: the SDK SimMirror uses now.
        self._adb_path = adb_path
        self._starter = starter

    async def _connect(self) -> Streams:
        try:
            return await asyncio.wait_for(self._open(self.address), self._timeout)
        except (OSError, asyncio.TimeoutError, TimeoutError) as exc:
            raise AdbError(f"the adb server does not answer at {self.address}: {exc or 'timed out'}") from exc

    @staticmethod
    async def _request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, service: str) -> None:
        data = service.encode()
        writer.write(b"%04x" % len(data) + data)
        await writer.drain()
        status = await reader.readexactly(4)
        if status == b"OKAY":
            return
        if status == b"FAIL":
            length = int(await reader.readexactly(4), 16)
            said = (await reader.readexactly(length)).decode(errors="replace")
            raise AdbError(said or f"adb refused {service.split(':')[0]}")
        raise AdbError(f"the adb server answered {status!r}")

    async def _host(self, service: str) -> str:
        reader, writer = await self._connect()
        try:
            await self._request(reader, writer, service)
            length = int(await reader.readexactly(4), 16)
            return (await reader.readexactly(length)).decode(errors="replace")
        except (asyncio.IncompleteReadError, ValueError, OSError) as exc:
            raise AdbError(f"the adb server's answer to {service} was cut short") from exc
        finally:
            await _close(writer)

    async def version(self) -> int:
        """The adb server's protocol version: 41 for adb 1.0.41."""
        return int(await asyncio.wait_for(self._host("host:version"), self._timeout), 16)

    async def ensure_server(self) -> None:
        """Start the adb server when nothing answers -- as adb itself does before its first command."""
        try:
            await self.version()
            return
        except AdbError:
            adb = self._adb_path()
            if adb is None or not adb.is_file():
                raise
        code, out = await self._starter((str(adb), "start-server"))
        if code != 0:
            raise AdbError(f"the adb server could not be started: {out.strip() or f'adb exited with {code}'}")
        await self.version()

    async def devices(self) -> list[AdbDevice]:
        """Every device adb knows now, whatever state it is in."""
        return parse_devices(await asyncio.wait_for(self._host("host:devices-l"), self._timeout))

    async def track(self) -> AsyncIterator[list[AdbDevice]]:
        """The devices adb knows, now and each time that changes, until the iterator is closed."""
        reader, writer = await self._connect()
        try:
            await self._request(reader, writer, "host:track-devices-l")
            while True:
                length = int(await reader.readexactly(4), 16)
                yield parse_devices((await reader.readexactly(length)).decode(errors="replace"))
        except (asyncio.IncompleteReadError, ValueError, OSError) as exc:
            raise AdbError("the adb server stopped telling which devices it knows") from exc
        finally:
            await _close(writer)

    async def open(self, serial: str, service: str) -> Streams:
        """A connection that is a service on a device: what is sent and read is the service's own."""
        reader, writer = await self._connect()
        try:
            await self._request(reader, writer, f"host:transport:{serial}")
            await self._request(reader, writer, service)
        except (asyncio.IncompleteReadError, OSError) as exc:
            await _close(writer)
            raise AdbError(f"{serial} could not be reached through adb") from exc
        except AdbError:
            await _close(writer)
            raise
        return reader, writer

    async def shell(self, serial: str, command: str | Sequence[str], *, timeout: float | None = None) -> ShellResult:
        """Run a command in the device's shell and answer how it exited, with what it wrote."""
        return await asyncio.wait_for(self._shell(serial, command_line(command)), timeout or self._timeout)

    async def _shell(self, serial: str, command: str) -> ShellResult:
        reader, writer = await self.open(serial, f"shell,v2,raw:{command}")
        out, err = bytearray(), bytearray()
        try:
            while True:
                kind, length = _SHELL_PACKET.unpack(await reader.readexactly(_SHELL_PACKET.size))
                payload = await reader.readexactly(length)
                if kind == _STDOUT:
                    out += payload
                elif kind == _STDERR:
                    err += payload
                elif kind == _EXIT:
                    return ShellResult(payload[0] if payload else 0, bytes(out), bytes(err))
        except asyncio.IncompleteReadError as exc:
            raise AdbError(f"{serial} stopped answering while it ran {command.split(' ')[0]}") from exc
        finally:
            await _close(writer)

    async def run(self, serial: str, command: str | Sequence[str], *, timeout: float | None = None) -> str:
        """A command's output -- or `AdbError` with what it said, when it failed."""
        result = await self.shell(serial, command, timeout=timeout)
        if result.code != 0:
            raise AdbError(result.message)
        return result.text

    async def exec_out(
        self, serial: str, command: str | Sequence[str], *, data: bytes = b"", timeout: float | None = None
    ) -> bytes:
        """A command's raw output, its input `data` sent first: a screenshot's bytes, an APK installed from stdin."""

        async def read() -> bytes:
            reader, writer = await self.open(serial, f"exec:{command_line(command)}")
            try:
                if data:
                    writer.write(data)
                    await writer.drain()
                    with contextlib.suppress(OSError):
                        writer.write_eof()
                return await reader.read()
            finally:
                await _close(writer)

        return await asyncio.wait_for(read(), timeout or self._timeout)

    async def stat(self, serial: str, remote: str) -> FileStat:
        """A file on the device: its mode, size and when it changed; a mode of 0 when it is not there."""
        reader, writer = await self.open(serial, "sync:")
        try:
            path = remote.encode()
            writer.write(_SYNC.pack(b"STAT", len(path)) + path)
            await writer.drain()
            answer = await asyncio.wait_for(reader.readexactly(16), self._timeout)
            if answer[:4] != b"STAT":
                raise AdbError(f"adb answered {answer[:4]!r} to a file's stat")
            mode, size, mtime = struct.unpack("<III", answer[4:])
            writer.write(_SYNC.pack(b"QUIT", 0))
            return FileStat(mode, size, mtime)
        finally:
            await _close(writer)

    async def push(self, serial: str, data: bytes, remote: str, *, mode: int = 0o644, mtime: int = 0) -> None:
        """Write `data` to a file on the device."""
        reader, writer = await self.open(serial, "sync:")
        try:
            spec = f"{remote},{mode}".encode()
            writer.write(_SYNC.pack(b"SEND", len(spec)) + spec)
            for start in range(0, len(data), SYNC_CHUNK):
                chunk = data[start : start + SYNC_CHUNK]
                writer.write(_SYNC.pack(b"DATA", len(chunk)) + chunk)
                await writer.drain()
            writer.write(_SYNC.pack(b"DONE", mtime))
            await writer.drain()
            status, length = _SYNC.unpack(await asyncio.wait_for(reader.readexactly(8), self._timeout))
            if status != b"OKAY":
                said = (
                    (await reader.readexactly(length)).decode(errors="replace") if status == b"FAIL" else repr(status)
                )
                raise AdbError(f"{remote} could not be written on the device: {said}")
            writer.write(_SYNC.pack(b"QUIT", 0))
        finally:
            await _close(writer)

    async def install(self, serial: str, apks: Sequence[Path], *, timeout: float = 300.0) -> None:
        """Install an app -- one APK, or the split APKs one app is made of -- replacing any copy already there, and
        allowing a test build (``-t``), as Android Studio does."""
        if len(apks) == 1:
            data = apks[0].read_bytes()
            said = await self.exec_out(
                serial, ["cmd", "package", "install", "-r", "-t", "-S", str(len(data))], data=data, timeout=timeout
            )
            _installed(said, apks[0].name)
            return
        sizes = [apk.stat().st_size for apk in apks]
        created = await self.run(serial, ["cmd", "package", "install-create", "-r", "-t", "-S", str(sum(sizes))])
        session = created[created.find("[") + 1 : created.find("]")]
        if not session.isdigit():
            raise AdbError(f"the device would not start installing: {created.strip()}")
        for index, (apk, size) in enumerate(zip(apks, sizes, strict=True)):
            said = await self.exec_out(
                serial,
                ["cmd", "package", "install-write", "-S", str(size), session, f"{index}_{apk.name}", "-"],
                data=apk.read_bytes(),
                timeout=timeout,
            )
            _installed(said, apk.name)
        _installed(await self.exec_out(serial, ["cmd", "package", "install-commit", session], timeout=timeout), "app")


def _installed(said: bytes, what: str) -> None:
    text = said.decode(errors="replace").strip()
    if "Success" not in text:
        raise AdbError(f"{what} was not installed: {text.splitlines()[-1] if text else 'the device said nothing'}")


class ShellStream:
    """A long-running command on a device -- a server it keeps running -- what it has said, and its pid.

    Closing adb's connection does not end the command on the device (measured: a ``sleep`` outlived it), so the
    command is started as ``echo pid:$$; exec <command>`` -- the shell becomes the command, keeping its pid -- and
    `close` ends that pid.
    """

    PID_MARK = "pid:"

    def __init__(self, adb: Adb, serial: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._adb = adb
        self._serial = serial
        self._reader = reader
        self._writer = writer
        self.said = bytearray()
        self.exit_code: int | None = None
        self.pid: int | None = None
        self._pid_said = asyncio.Event()
        self._closed = False
        self._task = asyncio.get_running_loop().create_task(self._listen())

    @classmethod
    async def start(cls, adb: Adb, serial: str, command: str | Sequence[str], *, timeout: float = 5.0) -> ShellStream:
        """Start a command, once its pid is known."""
        reader, writer = await adb.open(serial, f"shell,v2,raw:echo {cls.PID_MARK}$$; exec {command_line(command)}")
        stream = cls(adb, serial, reader, writer)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(stream._pid_said.wait(), timeout)
        if stream.pid is None:
            await stream.close()
            raise AdbError(f"{serial} did not start the command: {stream.last_said or 'it said nothing'}")
        return stream

    async def _listen(self) -> None:
        try:
            while True:
                kind, length = _SHELL_PACKET.unpack(await self._reader.readexactly(_SHELL_PACKET.size))
                payload = await self._reader.readexactly(length)
                if kind == _EXIT:
                    self.exit_code = payload[0] if payload else 0
                    return
                self._heard(payload)
        except (asyncio.IncompleteReadError, OSError):
            return
        finally:
            self._pid_said.set()

    def _heard(self, payload: bytes) -> None:
        if self.pid is None:
            first, _, _rest = bytes(self.said + payload).partition(b"\n")
            text = first.decode(errors="replace").strip()
            if text.startswith(self.PID_MARK) and text[len(self.PID_MARK) :].isdigit():
                self.pid = int(text[len(self.PID_MARK) :])
                self._pid_said.set()
        self.said += payload
        del self.said[:-4096]

    @property
    def running(self) -> bool:
        return not self._task.done()

    @property
    def last_said(self) -> str:
        """The last line the command wrote, for a refusal."""
        lines = self.said.decode(errors="replace").strip().splitlines()
        return lines[-1] if lines else ""

    async def close(self) -> None:
        """End the command on the device and let go of it. Closing twice is closing once."""
        if self._closed:
            return
        self._closed = True
        if self.pid is not None and self.running:
            with contextlib.suppress(AdbError, asyncio.TimeoutError, TimeoutError):
                await self._adb.shell(self._serial, ["kill", str(self.pid)], timeout=5.0)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await _close(self._writer)
