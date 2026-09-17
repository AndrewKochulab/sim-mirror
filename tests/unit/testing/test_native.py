# SPDX-License-Identifier: Apache-2.0
"""The shipped stand-in for the native helper answers as the Swift one does, misbehaves when told to, and ends."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncIterator
from pathlib import Path

from sim_mirror.connectors.base import Screen
from sim_mirror.connectors.native import wire
from sim_mirror.testing.fakes import KEY_FRAME, FakeEngine
from sim_mirror.testing.native import FakeHelper, FakeHelperProcess, FakeHelperSpawn, short_run_dir


async def ask(path: Path, *frames: bytes) -> list[wire.Frame]:
    """Send frames on one connection and read every frame until the helper closes it or goes quiet."""
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        for frame in frames:
            writer.write(frame)
        await writer.drain()
        answers: list[wire.Frame] = []
        while True:
            try:
                answer = await asyncio.wait_for(wire.read_frame(reader), 0.2)
            except (asyncio.TimeoutError, wire.WireError, OSError):
                return answers
            if answer is None:
                return answers
            answers.append(answer)
    finally:
        writer.close()
        await writer.wait_closed()


class Finite(FakeEngine):
    """A stream that ends, as one whose encoder the helper let go of."""

    async def _finite(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        return self._finite()


class Unplugged(FakeEngine):
    async def describe(self) -> Screen:
        raise ConnectionResetError("the peer went away")


async def test_a_request_it_does_not_know_is_refused_and_a_finished_stream_ends_with_no_more_chunks() -> None:
    helper = FakeHelper(Finite())
    with short_run_dir() as run:
        await helper.serve(run / "h.sock")
        try:
            answers = await ask(
                run / "h.sock",
                wire.request(1, "jump"),
                wire.request(2, "stream", fps=1, scale=1, key_frame_s=1, bitrate=1),
            )
            assert answers[0].kind == wire.FAILURE and answers[0].document()["message"].endswith("knows: jump")
            assert [(answer.kind, answer.blob) for answer in answers[1:]] == [(wire.CHUNK, KEY_FRAME)]
        finally:
            await helper.stop()
        await helper.stop()


async def test_what_is_not_a_frame_closes_the_connection_and_a_peer_gone_mid_answer_is_passed_over() -> None:
    helper = FakeHelper(Unplugged())
    with short_run_dir() as run:
        await helper.serve(run / "h.sock")
        try:
            assert await ask(run / "h.sock", b"\x00\x00\x00\x09\x07\x00\x00\x00\x01\x00\x00\x00\x00") == []
            assert await ask(run / "h.sock", wire.request(1, "describe")) == []
            assert helper.requests == [{"op": "describe"}]
        finally:
            await helper.stop()


async def test_a_stopped_process_keeps_how_it_ended_and_a_signal_for_another_pid_is_nobodys() -> None:
    helper = FakeHelper()
    process = FakeHelperProcess(helper, 7000)
    process.returncode = -signal.SIGKILL
    assert await process.wait() == -signal.SIGKILL
    spawn = FakeHelperSpawn(helper)
    spawn.processes.append(process)
    spawn.signal_group(1234, signal.SIGTERM)
    assert process.returncode == -signal.SIGKILL
