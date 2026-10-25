# SPDX-License-Identifier: Apache-2.0
"""scrcpy's server, played on a `testing.adb.FakeAdbServer`: no device, no JVM.

A server started on the stand-in adb (``app_process … com.genymobile.scrcpy.Server``) opens its abstract socket here.
A video server's first socket sends what release 5.0.1 sends -- its codec, a session packet with its size, the parameter
sets, a key frame, then a frame every `FRAME_S` -- and a key frame again whenever it is told to capture again; its
control socket, and a control server's, record every message they are sent (`messages`), decoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

from sim_mirror.connectors.android import scrcpy
from sim_mirror.connectors.android.scrcpy_source import ScrcpyJars, ScrcpyRelease
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.fakes import KEY_FRAME

#: The parameter sets and the key frame of `fakes.KEY_FRAME`, apart, as a server sends them.
CONFIG, IDR = KEY_FRAME[:-7], KEY_FRAME[-7:]
P_FRAME = b"\x00\x00\x00\x01\x41\x9a\x02"
#: A server's bytes, and the release they are: what a test's jars speak to.
TEST_JAR = b"PK\x03\x04 a scrcpy server stand-in"
TEST_RELEASE = ScrcpyRelease("9.9.9", len(TEST_JAR), hashlib.sha256(TEST_JAR).hexdigest())


def fetched_jars(root: Path, *, fetched: bool = True) -> ScrcpyJars:
    """scrcpy's server as a test has it: the test release, fetched under `root` unless `fetched` is false, and
    fetched again from bytes, never the network."""
    jars = ScrcpyJars(root=lambda: root, fetch=lambda url: TEST_JAR, release=TEST_RELEASE)
    if fetched:
        jars.fetch_now()
    return jars


FRAME_S = 0.02


@dataclass
class Message:
    """One control message, as the server reads it."""

    kind: str
    fields: tuple[object, ...] = ()


@dataclass
class FakeScrcpy:
    """scrcpy's server on every device a stand-in adb lists."""

    server: FakeAdbServer
    width: int = 1080
    height: int = 2424
    #: Every control message received, in order.
    messages: list[Message] = field(default_factory=list)
    #: How many times a video server was told to capture again.
    resets: int = 0
    #: A still screen: after its first key frame, the video sends nothing more until it captures again.
    still: bool = False
    #: The size the next video server says it encodes at, when a test changes it mid-stream.
    resize_to: tuple[int, int] | None = None
    #: Whether a server ends by itself once its sockets are closed, as scrcpy's does; off, it runs until killed.
    ends_itself: bool = True
    _opened: dict[str, int] = field(default_factory=dict)
    _open: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server.abstract["scrcpy_"] = self._serve

    def _command(self, scid: str) -> str:
        return next((command for command in self.server.running.values() if f"scid={scid}" in command), "")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        name = self.server.opening.removeprefix("scrcpy_")
        command = self._command(name)
        opened = self._opened.get(name, 0)
        self._opened[name] = opened + 1
        video = "video=true" in command and opened == 0
        self._open[name] = self._open.get(name, 0) + 1
        try:
            if video:
                await self._video(reader, writer)
            else:
                await self._control(reader, writer)
        finally:
            self._open[name] -= 1
            if not self._open[name] and self.ends_itself:
                self._end(name)

    def _end(self, scid: str) -> None:
        """The server whose sockets all closed ends, as scrcpy's does: its shell is told it exited."""
        for pid, command in list(self.server.running.items()):
            if f"scid={scid}" in command:
                self.server.running.pop(pid)

    async def _video(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        width, height = self.width, self.height
        writer.write(struct.pack(">I", scrcpy.H264))
        writer.write(scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | width, height))
        writer.write(scrcpy.HEADER.pack(scrcpy.CONFIG_FLAG, len(CONFIG)) + CONFIG)
        writer.write(scrcpy.HEADER.pack(scrcpy.KEY_FRAME_FLAG, len(IDR)) + IDR)
        with contextlib.suppress(ConnectionError, RuntimeError):
            await writer.drain()
            while not reader.at_eof():
                if self.resize_to is not None:
                    w, h = self.resize_to
                    writer.write(scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | w, h))
                if not self.still:
                    writer.write(scrcpy.HEADER.pack(1, len(P_FRAME)) + P_FRAME)
                await writer.drain()
                await asyncio.sleep(FRAME_S)

    def captured_again(self) -> None:
        self.resets += 1

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # A device message, as a device sends its clipboard: what reads the control socket lets it go.
        writer.write(b"\x00" + struct.pack(">I", 4) + b"clip")
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError):
            await writer.drain()
            while True:
                kind = (await reader.readexactly(1))[0]
                self.messages.append(await self._message(kind, reader))

    async def _message(self, kind: int, reader: asyncio.StreamReader) -> Message:
        if kind == scrcpy.INJECT_TOUCH:
            rest = await reader.readexactly(scrcpy._TOUCH.size - 1)
            _, action, _pointer, x, y, w, h, pressure, _button, _buttons = scrcpy._TOUCH.unpack(bytes([kind]) + rest)
            return Message("touch", (action, x, y, w, h, pressure))
        if kind == scrcpy.INJECT_KEYCODE:
            rest = await reader.readexactly(scrcpy._KEY.size - 1)
            _, action, code, _repeat, meta = scrcpy._KEY.unpack(bytes([kind]) + rest)
            return Message("key", (action, code, meta))
        if kind == scrcpy.INJECT_TEXT:
            (length,) = struct.unpack(">I", await reader.readexactly(4))
            return Message("text", ((await reader.readexactly(length)).decode(),))
        if kind == scrcpy.SET_CLIPBOARD:
            _sequence, paste, length = struct.unpack(">qBI", await reader.readexactly(13))
            return Message("clipboard", ((await reader.readexactly(length)).decode(), bool(paste)))
        if kind == scrcpy.RESET_VIDEO:
            self.captured_again()
            return Message("reset")
        return Message(f"unknown {kind}")
