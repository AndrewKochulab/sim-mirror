# SPDX-License-Identifier: Apache-2.0
"""The native helper without a simulator: its wire protocol, served on a real unix socket from a `FakeEngine`.

`FakeHelper` is what ``sim-mirror-helper serve`` is to SimMirror -- a socket answering hellos, screenshots, the
accessibility document, input and H.264 streams -- so a test drives the native connector's own client, launcher and
wire code end to end, and only the Swift side is stood in for. `FakeHelperSpawn` stands where `process.spawn` does:
"starting" the helper serves a `FakeHelper` on the socket its argv names.

A unix socket's path may have only 104 bytes, and a test's temporary folder often has more, so `short_run_dir` gives a
folder with a short path that is removed afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent
from sim_mirror.connectors.native import wire
from sim_mirror.testing.fakes import FakeEngine


@contextlib.contextmanager
def short_run_dir() -> Iterator[Path]:
    """A private folder with a path short enough for unix sockets, removed afterwards."""
    folder = Path(tempfile.mkdtemp(prefix="smh-", dir="/tmp"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class FakeHelper:
    """A native helper serving a `FakeEngine`, as the Swift one serves a simulator."""

    def __init__(
        self,
        engine: FakeEngine | None = None,
        *,
        hid: str | None = "dtuhid",
        reasons: Sequence[str] = (),
        version: str = __version__,
        wire_version: int = wire.VERSION,
        core_simulator: str | None = "1171.7",
    ) -> None:
        self.engine = engine or FakeEngine()
        self.hid = hid
        self.reasons = tuple(reasons)
        self.version = version
        self.wire_version = wire_version
        self.core_simulator = core_simulator
        #: Every request, in the order it arrived.
        self.requests: list[dict[str, Any]] = []
        #: Frames sent back as they are, in place of the answer to the next requests: for a test of what the client
        #: does with a helper that misbehaves.
        self.raw_answers: list[bytes] = []
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.StreamWriter] = set()

    async def serve(self, socket_path: Path) -> None:
        self._server = await asyncio.start_unix_server(self._connection, path=str(socket_path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._connections):
            writer.close()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._connections.add(writer)
        tasks: set[asyncio.Task[None]] = set()
        try:
            while (frame := await wire.read_frame(reader)) is not None:
                request = frame.document()
                self.requests.append(request)
                if self.raw_answers:
                    writer.write(self.raw_answers.pop(0))
                    continue
                task = asyncio.ensure_future(self._answer(frame.id, request, writer))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                if request.get("op") == "hid":
                    await task
        except (OSError, wire.WireError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            self._connections.discard(writer)
            writer.close()

    async def _answer(self, request_id: int, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        def send(kind: int, document: Mapping[str, Any] | None = None, blob: bytes = b"") -> None:
            body = json.dumps(document).encode() if document is not None else b""
            writer.write(wire.encode(wire.Frame(kind, request_id, body, blob)))

        try:
            op = request.get("op")
            if op == "hello":
                send(wire.REPLY, self._hello(await self.engine.describe()))
            elif op == "describe":
                send(wire.REPLY, _screen(await self.engine.describe()))
            elif op == "screenshot":
                crop = _crop(request.get("crop"))
                shot = await self.engine.screenshot(
                    max_width=int(request["max_width"]), quality=int(request["quality"]), crop=crop
                )
                send(wire.REPLY, {"width": shot.width, "height": shot.height}, shot.jpeg)
            elif op == "accessibility":
                send(wire.REPLY, await self.engine.accessibility())
            elif op == "hid":
                await self.engine.hid(_events(request.get("events") or []))
                send(wire.REPLY, {})
            elif op == "stream":
                async for chunk in self.engine.h264(
                    fps=request["fps"], scale=request["scale"], key_frame_s=request["key_frame_s"],
                    bitrate=request["bitrate"],
                ):  # fmt: skip
                    send(wire.CHUNK, None, chunk)
                    await writer.drain()
            else:
                send(wire.FAILURE, {"message": f"not a request this helper knows: {op}", "status": 400})
            await writer.drain()
        except ConnectorError as exc:
            send(wire.FAILURE, {"message": str(exc), "status": 502})
            with contextlib.suppress(OSError):
                await writer.drain()
        except OSError:
            pass

    def _hello(self, screen: Any) -> dict[str, Any]:
        return {
            "wire": self.wire_version,
            "version": self.version,
            "core_simulator": self.core_simulator,
            "hid": self.hid,
            "reasons": list(self.reasons),
            "screen": _screen(screen),
        }


def _screen(screen: Any) -> dict[str, Any]:
    return {
        "width_px": screen.width_px,
        "height_px": screen.height_px,
        "width_pt": screen.width_pt,
        "height_pt": screen.height_pt,
        "scale": screen.scale,
    }


def _crop(raw: Any) -> Crop | None:
    if not isinstance(raw, dict):
        return None
    return Crop(float(raw["x"]), float(raw["y"]), float(raw["width"]), float(raw["height"]))


async def _events(raw: list[dict[str, Any]]) -> AsyncIterator[HidEvent]:
    for event in raw:
        yield HidEvent(
            kind=event["kind"],
            phase=event["phase"],
            x=float(event.get("x", 0.0)),
            y=float(event.get("y", 0.0)),
            button=str(event.get("button", "")),
            code=int(event.get("code", 0)),
        )


class FakeHelperProcess:
    """A started helper's process, as `process.spawn` answers one."""

    def __init__(self, helper: FakeHelper, pid: int) -> None:
        self.helper = helper
        self.pid = pid
        self.returncode: int | None = None

    async def wait(self) -> int | None:
        await self.helper.stop()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeHelperSpawn:
    """Stands where `process.spawn` does: serves a `FakeHelper` on the socket the helper's argv names.

    Each start serves `helper`, or a fresh one over the same engine when a test lets go of one; `fail` refuses to start
    it, as a helper that cannot be run.
    """

    def __init__(self, helper: FakeHelper | None = None, *, fail: OSError | None = None) -> None:
        self.helper = helper or FakeHelper()
        self.fail = fail
        #: Each argv the helper was started with, and its log.
        self.started: list[tuple[tuple[str, ...], Path]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self.processes: list[FakeHelperProcess] = []

    async def __call__(
        self, argv: Sequence[str], log: Path, /, *, env: Mapping[str, str] | None = None
    ) -> FakeHelperProcess:
        self.started.append((tuple(argv), log))
        self.envs.append(env)
        if self.fail is not None:
            raise self.fail
        socket = Path(argv[argv.index("--socket") + 1])
        await self.helper.serve(socket)
        process = FakeHelperProcess(self.helper, 7000 + len(self.processes))
        self.processes.append(process)
        return process

    def signal_group(self, pid: int, sig: int) -> None:
        """Stands where `process.signal_group` does: the helper started with that pid exits."""
        for process in self.processes:
            if process.pid == pid:
                process.returncode = -sig
