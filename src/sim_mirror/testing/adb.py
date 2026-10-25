# SPDX-License-Identifier: Apache-2.0
"""A stand-in adb server, speaking adb's smart-socket protocol on a loopback port: no adb, no emulator, no phone.

A test names the devices it lists (`FakeAdbServer.device`) and what each command answers (`FakeAdbServer.on`, by
the start of its command line, the latest first); the server records every service asked for (`calls`) and every file
pushed (`files`). A long-running command -- one `ShellStream` starts -- says its pid and stays up until it is killed or
its connection closes. A device's abstract unix sockets are a test's handlers (`FakeAdbServer.abstract`), as scrcpy's
server is played by `testing.scrcpy`. An emulator's console is `FakeEmulatorConsole`.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from PIL import Image

from sim_mirror.platform.adb import Adb
from sim_mirror.testing.fakes import fixture

_SHELL_PACKET = struct.Struct("<BI")
_SYNC = struct.Struct("<4sI")
#: How `ShellStream` starts a long-running command.
_STREAMED = re.compile(r"\Aecho pid:\$\$; exec (.*)\Z", re.S)

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
#: What a command answers: its exit code, stdout and stderr -- or a function of the command's input that does.
Answer = tuple[int, bytes, bytes]
Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def small_jpeg(width: int = 108, height: int = 242, color: tuple[int, int, int] = (40, 90, 160)) -> bytes:
    """A screenshot a device could send: a JPEG of one color, a tenth of a Pixel 9's screen."""
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


@dataclass
class _Rule:
    prefix: str
    answer: Answer | Callable[[str, bytes], Answer]
    then: Callable[[], None] | None = None


@dataclass
class FakeDevice:
    serial: str
    state: str = "device"
    properties: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        words = " ".join(f"{key}:{value}" for key, value in self.properties.items())
        return f"{self.serial}\t{self.state} {words}".rstrip() + "\n"


class FakeAdbServer:
    """An adb server on 127.0.0.1, for one test."""

    def __init__(self) -> None:
        self.devices: dict[str, FakeDevice] = {}
        self._rules: list[_Rule] = []
        #: Every service asked for, with the serial it was asked of ("" for the server's own).
        self.calls: list[tuple[str, str]] = []
        #: Every file pushed, by serial and path.
        self.files: dict[tuple[str, str], bytes] = {}
        #: What was sent to each command that read its input: an APK being installed, say.
        self.inputs: list[tuple[str, bytes]] = []
        #: Handlers for a device's abstract unix sockets, by the start of their name.
        self.abstract: dict[str, Handler] = {}
        #: Long-running commands still up, by pid.
        self.running: dict[int, str] = {}
        self.version = 41
        self._pids = iter(range(31000, 1 << 30))
        self._trackers: list[asyncio.Queue[str]] = []
        self._sessions: set[asyncio.Task[Any]] = set()
        self._server: asyncio.AbstractServer | None = None
        self.address = ""

    # -- what a test sets ------------------------------------------------------------------------------------------

    def device(self, serial: str, state: str = "device", **properties: str) -> FakeAdbServer:
        """List a device, or change how one stands; trackers are told."""
        self.devices[serial] = FakeDevice(serial, state, dict(properties))
        self._tell()
        return self

    def emulator(self, avd: str = "Pixel_9", port: int = 5554, *, booted: bool = True) -> FakeAdbServer:
        """List a running emulator that answers what SimMirror asks of one: which AVD it is, its screen, a screenshot,
        its UI and its windows -- the Settings app on a Pixel 9, as captured from an API 36 emulator."""
        self.device(f"emulator-{port}", transport_id=str(port))
        return self.answering_as_android(f"{avd}\n16\n36\n", booted=booted)

    def phone(self, serial: str = "R58M12ABC", model: str = "Pixel_8", state: str = "device") -> FakeAdbServer:
        """List a phone connected by cable that answers as the emulator does, by its serial number."""
        self.device(serial, state, model=model, transport_id="2")
        self.on("getprop ro.serialno", out=f"{serial}\n15\n35\n")
        return self.answering_as_android(None)

    def answering_as_android(self, avd_said: str | None, *, booted: bool = True) -> FakeAdbServer:
        if avd_said is not None:
            self.on("getprop ro.boot.qemu.avd_name", out=avd_said)
        self.on("getprop sys.boot_completed", out="1\n" if booted else "0\n")
        self.on("wm size", out="Physical size: 1080x2424\n").on("wm density", out="Physical density: 420\n")
        self.on("dumpsys display", out="  mCurrentOrientation=0\n")
        self.on("dumpsys window policy", out="    KeyguardStateMonitor\n      mIsShowing=false\n")
        self.on("dumpsys window windows", out=fixture("android-window-settings.txt"))
        self.on("uiautomator dump", out=fixture("android-uiautomator-settings.xml"))
        self.on("screencap -j", out=small_jpeg())
        return self

    def unplug(self, serial: str) -> None:
        self.devices.pop(serial, None)
        self._tell()

    def on(
        self,
        prefix: str,
        *,
        out: bytes | str = b"",
        err: bytes | str = b"",
        code: int = 0,
        answer: Callable[[str, bytes], Answer] | None = None,
        then: Callable[[], None] | None = None,
    ) -> FakeAdbServer:
        """What a command starting with `prefix` answers, on any device; the latest rule that matches wins."""
        encoded = (code, out.encode() if isinstance(out, str) else out, err.encode() if isinstance(err, str) else err)
        self._rules.append(_Rule(prefix, answer or encoded, then))
        return self

    def commands(self, serial: str | None = None) -> list[str]:
        """The commands run, in order, on one device or on any."""
        found = []
        for asked, service in self.calls:
            if serial is not None and asked != serial:
                continue
            for kind in ("shell,v2,raw:", "exec:"):
                if service.startswith(kind):
                    found.append(service[len(kind) :])
        return found

    def adb(self, **options: Any) -> Adb:
        return Adb(self.address, **options)

    # -- serving -----------------------------------------------------------------------------------------------------

    async def __aenter__(self) -> FakeAdbServer:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.address = f"127.0.0.1:{port}"
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        assert self._server is not None
        self._server.close()
        for session in list(self._sessions):
            session.cancel()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()

    def _tell(self) -> None:
        listing = self._listing()
        for queue in self._trackers:
            queue.put_nowait(listing)

    def _listing(self) -> str:
        return "".join(device.line() for device in self.devices.values())

    def _answer(self, command: str, data: bytes) -> Answer:
        for rule in reversed(self._rules):
            if command.startswith(rule.prefix):
                if rule.then is not None:
                    rule.then()
                return rule.answer(command, data) if callable(rule.answer) else rule.answer
        return 0, b"", b""

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._sessions.add(task)
        try:
            await self._session(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._sessions.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _request(reader: asyncio.StreamReader) -> str:
        length = int(await reader.readexactly(4), 16)
        return (await reader.readexactly(length)).decode()

    @staticmethod
    def _okay(writer: asyncio.StreamWriter) -> None:
        writer.write(b"OKAY")

    @staticmethod
    def _fail(writer: asyncio.StreamWriter, why: str) -> None:
        data = why.encode()
        writer.write(b"FAIL" + b"%04x" % len(data) + data)

    @staticmethod
    def _reply(writer: asyncio.StreamWriter, text: str) -> None:
        data = text.encode()
        writer.write(b"OKAY" + b"%04x" % len(data) + data)

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        service = await self._request(reader)
        self.calls.append(("", service))
        if service == "host:version":
            self._reply(writer, f"{self.version:04x}")
        elif service == "host:devices-l":
            self._reply(writer, self._listing())
        elif service == "host:track-devices-l":
            await self._track(writer)
        elif service.startswith("host:transport:"):
            serial = service.removeprefix("host:transport:")
            device = self.devices.get(serial)
            if device is None or device.state != "device":
                self._fail(writer, f"device '{serial}' not found" if device is None else f"device {device.state}")
                return
            self._okay(writer)
            await self._device(serial, reader, writer)
        else:
            self._fail(writer, f"unknown host service {service}")
        await writer.drain()

    async def _track(self, writer: asyncio.StreamWriter) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._trackers.append(queue)
        self._okay(writer)
        try:
            listing = self._listing()
            while True:
                data = listing.encode()
                writer.write(b"%04x" % len(data) + data)
                await writer.drain()
                listing = await queue.get()
        finally:
            self._trackers.remove(queue)

    async def _device(self, serial: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        service = await self._request(reader)
        self.calls.append((serial, service))
        if service.startswith("shell,v2,raw:"):
            await self._shell(serial, service.removeprefix("shell,v2,raw:"), reader, writer)
        elif service.startswith("exec:"):
            await self._exec(service.removeprefix("exec:"), reader, writer)
        elif service == "sync:":
            self._okay(writer)
            await self._sync(serial, reader, writer)
        elif service.startswith("localabstract:"):
            name = service.removeprefix("localabstract:")
            handler = next((h for start, h in self.abstract.items() if name.startswith(start)), None)
            if handler is None:
                self._fail(writer, "could not connect to localabstract address")
                return
            self._okay(writer)
            await writer.drain()
            await handler(reader, writer)
        else:
            self._fail(writer, f"unknown service {service}")

    @staticmethod
    def _packet(writer: asyncio.StreamWriter, kind: int, data: bytes) -> None:
        writer.write(_SHELL_PACKET.pack(kind, len(data)) + data)

    async def _shell(
        self, serial: str, command: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._okay(writer)
        streamed = _STREAMED.match(command)
        if streamed is not None:
            await self._streamed(serial, streamed.group(1), reader, writer)
            return
        if command.startswith("kill "):
            for word in command.split()[1:]:
                if word.isdigit():
                    self.running.pop(int(word), None)
        code, out, err = self._answer(command, b"")
        if out:
            self._packet(writer, 1, out)
        if err:
            self._packet(writer, 2, err)
        self._packet(writer, 3, bytes([code]))
        await writer.drain()

    async def _streamed(
        self, serial: str, command: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """A long-running command: its pid said first, then what it answers, then up until killed or let go."""
        pid = next(self._pids)
        self.running[pid] = command
        self._packet(writer, 1, f"pid:{pid}\n".encode())
        code, out, err = self._answer(command, b"")
        if out:
            self._packet(writer, 1, out)
        if err:
            self._packet(writer, 2, err)
        await writer.drain()
        if code != 0:
            self.running.pop(pid, None)
            self._packet(writer, 3, bytes([code]))
            await writer.drain()
            return
        while pid in self.running:
            try:
                await asyncio.wait_for(reader.read(1), 0.02)
            except (asyncio.TimeoutError, TimeoutError):
                continue
            if reader.at_eof():
                return
        self._packet(writer, 3, b"\x8f")
        await writer.drain()

    async def _exec(self, command: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._okay(writer)
        size = re.search(r"-S (\d+)", command)
        data = await reader.readexactly(int(size.group(1))) if size else b""
        if data:
            self.inputs.append((command, data))
        _code, out, _err = self._answer(command, data)
        writer.write(out)
        await writer.drain()

    async def _sync(self, serial: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            kind, length = _SYNC.unpack(await reader.readexactly(8))
            if kind == b"STAT":
                path = (await reader.readexactly(length)).decode()
                data = self.files.get((serial, path))
                stat = (0o100644, len(data), 1_700_000_000) if data is not None else (0, 0, 0)
                writer.write(b"STAT" + struct.pack("<III", *stat))
            elif kind == b"SEND":
                path = (await reader.readexactly(length)).decode().rsplit(",", 1)[0]
                body = bytearray()
                while True:
                    part, size = _SYNC.unpack(await reader.readexactly(8))
                    if part == b"DONE":
                        break
                    body += await reader.readexactly(size)
                self.files[(serial, path)] = bytes(body)
                writer.write(_SYNC.pack(b"OKAY", 0))
            else:
                return
            await writer.drain()


class FakeEmulatorConsole:
    """An emulator's console on a loopback port: it asks for a token, answers commands, and records them."""

    def __init__(self, avd: str = "Pixel_9", token: str = "secret") -> None:
        self.avd = avd
        self.token = token
        #: Every command after auth, as sent.
        self.commands: list[str] = []
        #: Answers a test sets for a command, by its first word: lines, or ``KO: why``.
        self.answers: dict[str, str] = {}
        self.killed = False
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> FakeEmulatorConsole:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        assert self._server is not None
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"Android Console: Authentication required\r\nOK\r\n")
            authed = False
            while line := (await reader.readline()).decode().strip():
                if line.startswith("auth "):
                    authed = line == f"auth {self.token}"
                    writer.write(b"OK\r\n" if authed else b"KO: authentication token does not match\r\n")
                elif not authed:
                    writer.write(b"KO: unknown command, try 'help'\r\n")
                else:
                    self.commands.append(line)
                    writer.write(self._said(line))
                    if line == "kill":
                        self.killed = True
                        await writer.drain()
                        return
                await writer.drain()
        finally:
            writer.close()

    def _said(self, line: str) -> bytes:
        word = line.split()[0]
        if word in self.answers:
            set_by_test = self.answers[word]
            return (set_by_test + "\r\n" + ("" if set_by_test.startswith("KO") else "OK\r\n")).encode()
        if line == "avd name":
            return f"{self.avd}\r\nOK\r\n".encode()
        if line == "kill":
            return b"OK: killing emulator, bye bye\r\n"
        return b"OK\r\n"
