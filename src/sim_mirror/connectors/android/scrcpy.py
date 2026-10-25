# SPDX-License-Identifier: Apache-2.0
"""scrcpy's server on a device: started, reached, read and written -- its protocol, as release 5.0.1 speaks it.

**Starting** pushes the server to ``/data/local/tmp/sim-mirror-scrcpy-<host>.jar`` (once: its size and modification
time are compared) and runs it with ``app_process`` through adb's shell, with ``cleanup=false`` -- with cleanup on, a
server deletes its own file as it starts (measured), and the next would find none. The server listens on an abstract
unix socket, ``scrcpy_<scid>`` in hex, reached through adb's transport: nothing listens on this Mac. It takes a second
or two to start (its JVM), so the socket is asked for until it answers. A server is ended by its pid
(`platform.adb.ShellStream`): letting go of adb's connection alone leaves it running.

**Video** (``video=true``): a codec id (``h264``), then packets, each with a 12-byte header in network order:

* a *session* packet -- its first bit set -- says the size the screen is encoded at, once per capture;
* a *config* packet (bit 62) carries the H.264 parameter sets (SPS, PPS);
* a *media* packet carries one encoded frame, a key frame when bit 61 is set, Annex-B.

Each key frame is sent with the parameter sets in front of it, as `core.frames` needs a viewer to start from one.

**Control** (``video=false``): messages to the server, in network order -- a touch, a key, text, the clipboard. With no
video, a touch's coordinates are the display's own pixels.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import struct
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.platform.adb import Adb, AdbError, ShellStream

SERVER_CLASS = "com.genymobile.scrcpy.Server"
#: How long a server has to open its socket: its JVM takes one to two seconds.
START_TIMEOUT_S = 8.0
START_POLL_S = 0.05
H264 = 0x68323634
#: A packet header: its PTS and flags, then its size.
HEADER = struct.Struct(">QI")
SESSION_FLAG = 1 << 63
CONFIG_FLAG = 1 << 62
KEY_FRAME_FLAG = 1 << 61
#: The largest packet a server is trusted to send.
PACKET_MAX = 16 << 20

#: Control message types and touch actions.
INJECT_KEYCODE, INJECT_TEXT, INJECT_TOUCH, SET_CLIPBOARD, RESET_VIDEO = 0, 1, 2, 9, 17
ACTION_DOWN, ACTION_UP, ACTION_MOVE = 0, 1, 2
#: The pointer a finger is: scrcpy's own id for a finger, not the mouse.
FINGER = -2
_TOUCH = struct.Struct(">BBqiiHHHii")
_KEY = struct.Struct(">BBiii")
#: The most text one message types.
TEXT_MAX = 300


def remote_jar(owner_tag: str) -> str:
    """Where a host's server is kept on the device: named for the host, so ending its servers ends no one else's."""
    safe = "".join(character if character.isalnum() else "-" for character in owner_tag) or "host"
    return f"/data/local/tmp/sim-mirror-scrcpy-{safe}.jar"


def server_command(remote: str, version: str, scid: int, options: dict[str, str]) -> list[str]:
    """The command that runs a server: through ``env``, so the shell that runs it becomes it and keeps its pid."""
    fixed = {
        "scid": f"{scid:08x}",
        "log_level": "warn",
        "tunnel_forward": "true",
        "audio": "false",
        "cleanup": "false",
        "send_device_meta": "false",
        "send_dummy_byte": "false",
        "clipboard_autosync": "false",
    }
    return [
        "env",
        f"CLASSPATH={remote}",
        "app_process",
        "/",
        SERVER_CLASS,
        version,
        *(f"{key}={value}" for key, value in {**fixed, **options}.items()),
    ]


def video_options(*, max_size: int, fps: int, bitrate: int, key_frame_s: float) -> dict[str, str]:
    """A video server's options: H.264 at most `max_size` on its long side, kept upright, with key frames as often as
    a viewer needs one to start from."""
    return {
        "video": "true",
        "control": "false",
        "video_codec": "h264",
        "max_size": str(max(8, max_size - max_size % 8)),
        "max_fps": str(fps),
        "video_bit_rate": str(bitrate),
        "video_codec_options": f"i-frame-interval:float={key_frame_s:g}",
        "capture_orientation": "@0",
    }


CONTROL_OPTIONS = {"video": "false", "control": "true"}


class SizeChanged(ConnectorError):
    """The screen is encoded at another size: the device's screen changed, and is to be described again."""


async def h264_units(reader: asyncio.StreamReader) -> AsyncIterator[tuple[bytes, int, int]]:
    """A video socket's frames: each Annex-B, a key frame with the parameter sets in front -- with the size it is
    encoded at. Raises `SizeChanged` when the size changes."""
    try:
        codec = struct.unpack(">I", await reader.readexactly(4))[0]
        if codec != H264:
            raise ConnectorError(f"scrcpy's server sent video as {codec:#x}, not H.264")
        size: tuple[int, int] | None = None
        config = b""
        while True:
            flags, length = HEADER.unpack(await reader.readexactly(HEADER.size))
            if flags & SESSION_FLAG:
                encoded = (flags & 0xFFFFFFFF, length)
                if size is not None and encoded != size:
                    raise SizeChanged(f"the screen is encoded at {encoded[0]}x{encoded[1]} now")
                size = encoded
                continue
            if length > PACKET_MAX:
                raise ConnectorError(f"scrcpy's server sent a packet of {length} bytes")
            payload = await reader.readexactly(length)
            if flags & CONFIG_FLAG:
                config = payload
                continue
            width, height = size or (0, 0)
            yield (config + payload if flags & KEY_FRAME_FLAG else payload), width, height
    except asyncio.IncompleteReadError as exc:
        raise ConnectorError("scrcpy's video stream ended") from exc


def reset_video() -> bytes:
    """Start capturing again: the next frame is a key frame, with the parameter sets in front."""
    return struct.pack(">B", RESET_VIDEO)


def touch(action: int, x: int, y: int, width: int, height: int) -> bytes:
    """A finger going down, moving or lifting at a point of the display; full pressure while down."""
    pressure = 0 if action == ACTION_UP else 0xFFFF
    # A finger presses no mouse button: both button fields are 0.
    return _TOUCH.pack(INJECT_TOUCH, action, FINGER, x, y, width, height, pressure, 0, 0)


def key(action: int, keycode: int, meta: int = 0) -> bytes:
    return _KEY.pack(INJECT_KEYCODE, action, keycode, 0, meta)


def text(typed: str) -> bytes:
    data = typed.encode()
    return struct.pack(">BI", INJECT_TEXT, len(data)) + data


def clipboard(sequence: int, value: str, *, paste: bool) -> bytes:
    """Put text on the device's clipboard, pasting it where the keyboard's focus is when `paste`."""
    data = value.encode()
    return struct.pack(">BqBI", SET_CLIPBOARD, sequence, 1 if paste else 0, len(data)) + data


@dataclass
class ScrcpyServer:
    """A server running on a device, and the sockets it serves: video's, then control's, as its options asked."""

    stream: ShellStream
    sockets: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]]

    @property
    def reader(self) -> asyncio.StreamReader:
        return self.sockets[0][0]

    @property
    def writer(self) -> asyncio.StreamWriter:
        return self.sockets[0][1]

    @property
    def running(self) -> bool:
        return self.stream.running and not any(writer.is_closing() for _reader, writer in self.sockets)

    async def close(self) -> None:
        """End the server and let go of its sockets. Closing twice is closing once."""
        for _reader, writer in self.sockets:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        await self.stream.close()


class ScrcpyLauncher:
    """Starts a host's scrcpy servers on devices: the server pushed once, run, and its socket reached."""

    def __init__(
        self,
        adb: Adb,
        owner_tag: str,
        *,
        start_timeout: float = START_TIMEOUT_S,
        scid: Callable[[], int] | None = None,
    ) -> None:
        self._adb = adb
        self.remote = remote_jar(owner_tag)
        self._start_timeout = start_timeout
        self._scid = scid or (lambda: random.getrandbits(31))

    async def push(self, serial: str, jar: bytes, mtime: int) -> None:
        """Put the server on the device, unless the one there is it already."""
        there = await self._adb.stat(serial, self.remote)
        if there.exists and there.size == len(jar) and there.mtime == mtime:
            return
        await self._adb.push(serial, jar, self.remote, mtime=mtime)

    async def start(self, serial: str, version: str, options: dict[str, str], *, sockets: int = 1) -> ScrcpyServer:
        """Run a server, and reach its sockets -- video's then control's, `sockets` of them -- or `ConnectorError`
        with what it said."""
        scid = self._scid()
        try:
            stream = await ShellStream.start(self._adb, serial, server_command(self.remote, version, scid, options))
        except AdbError as exc:
            raise ConnectorError(f"scrcpy's server could not be started: {exc}") from exc
        deadline = asyncio.get_running_loop().time() + self._start_timeout
        name = f"localabstract:scrcpy_{scid:08x}"
        while stream.running:
            try:
                first = await self._adb.open(serial, name)
            except AdbError:
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(START_POLL_S)
                continue
            server = ScrcpyServer(stream, [first])
            try:
                for _ in range(sockets - 1):
                    server.sockets.append(await self._adb.open(serial, name))
            except AdbError as exc:
                await server.close()
                raise ConnectorError(f"scrcpy's server did not open its control socket: {exc}") from exc
            return server
        said = stream.last_said or "it did not open its socket"
        await stream.close()
        raise ConnectorError(f"scrcpy's server did not start: {said}")

    async def reap(self, serials: Sequence[str]) -> int:
        """End this host's servers a crashed run left on these devices. Answers how many devices had any."""
        ended = 0
        for serial in serials:
            with contextlib.suppress(AdbError, asyncio.TimeoutError, TimeoutError):
                found = await self._adb.shell(serial, ["pkill", "-f", self.remote])
                ended += found.code == 0
        return ended
