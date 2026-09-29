# SPDX-License-Identifier: Apache-2.0
"""A recording's pictures: from the device's own tool, which says when it starts and finalizes on SIGINT, or from the
frames SimMirror streams, each written with its time."""

from __future__ import annotations

import asyncio
import signal
import struct
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.core.frames import FrameHub, StreamSettings
from sim_mirror.core.recording_sources import FRAME_FILE_MAGIC, FRAME_HEADER, StreamRecording, ToolRecording
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.testing.fakes import SCREEN, FakeEngine, FakeProcess, ManualClock

KEY_FRAME = b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce\x00\x00\x00\x01\x65\x88"
FRAME = b"\x00\x00\x00\x01\x41\x9a"


class Tool:
    """A device's tool recording: a fake process whose log says what a test has it say."""

    def __init__(self, tmp_path: Path, *, says: str = "Recording started\n", writes: bool = True) -> None:
        self.raw = tmp_path / "rec.mp4"
        self.log = tmp_path / "rec.log"
        self.process = FakeProcess(5001)
        self.says = says
        self.writes = writes
        self.signals: list[tuple[int, int]] = []
        self.killed: list[Any] = []

    async def begin(self, path: Path, log: Path) -> FakeProcess:
        assert (path, log) == (self.raw, self.log)
        log.write_text(self.says)
        return self.process

    def signal_group(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        if self.writes:
            self.raw.write_bytes(b"movie")
        self.process.finish(0)

    async def kill(self, proc: Any) -> None:
        self.killed.append(proc)
        proc.finish(-9)

    def recording(self, clock: ManualClock, **changes: Any) -> ToolRecording:
        async def no_wait(delay: float) -> None:
            clock.now += delay

        return ToolRecording(
            self.begin,
            self.raw,
            self.log,
            started="Recording started",
            clock=clock,
            sleep=no_wait,
            signal_group=self.signal_group,
            kill=self.kill,
            **changes,
        )


async def test_a_tool_recording_starts_once_its_tool_says_so_and_stops_with_sigint(tmp_path: Path) -> None:
    clock = ManualClock(100.0)
    tool = Tool(tmp_path)
    recording = tool.recording(clock)
    assert recording.kind == "movie" and await recording.start() == 100.0
    await recording.stop()
    await recording.stop()
    assert tool.signals == [(5001, signal.SIGINT)] and tool.raw.read_bytes() == b"movie"


async def test_a_tool_that_ends_or_never_starts_is_refused_and_one_that_writes_nothing_says_so(tmp_path: Path) -> None:
    clock = ManualClock()
    ended = Tool(tmp_path, says="simctl: device is not booted\n")
    ended.process.finish(1)
    with pytest.raises(DeviceControlError, match="did not start: simctl: device is not booted"):
        await ended.recording(clock).start()
    silent = Tool(tmp_path, says="")
    with pytest.raises(DeviceControlError, match="did not start within 1s"):
        await silent.recording(clock, start_timeout=1).start()
    assert silent.killed == [silent.process]
    empty = Tool(tmp_path, writes=False)
    (tmp_path / "rec.mp4").unlink(missing_ok=True)
    recording = empty.recording(clock)
    await recording.start()
    with pytest.raises(DeviceControlError, match="wrote no movie"):
        await recording.stop()


async def test_a_tool_that_does_not_finish_in_time_is_ended(tmp_path: Path) -> None:
    tool = Tool(tmp_path)

    def ignore(pid: int, sig: int) -> None:
        tool.raw.write_bytes(b"movie")

    recording = ToolRecording(
        tool.begin,
        tool.raw,
        tool.log,
        started="Recording started",
        signal_group=ignore,
        kill=tool.kill,
        stop_timeout=0.01,
    )
    await recording.start()
    await recording.stop()
    assert tool.killed == [tool.process]
    assert await ToolRecording(tool.begin, tool.raw, tool.log, started="x").stop() is None


def frames(raw: Path) -> list[tuple[int, float, bytes]]:
    data = raw.read_bytes()
    assert data.startswith(FRAME_FILE_MAGIC)
    found, at = [], len(FRAME_FILE_MAGIC)
    while at < len(data):
        kind, time, length = FRAME_HEADER.unpack_from(data, at)
        at += FRAME_HEADER.size
        found.append((kind, time, data[at : at + length]))
        at += length
    return found


async def test_a_stream_recording_writes_each_frame_the_screen_streams_with_its_time(tmp_path: Path) -> None:
    engine = FakeEngine()
    engine.chunks = [FRAME, KEY_FRAME, FRAME]
    hub = FrameHub(engine, SCREEN, StreamSettings(fps=30, quality=70, max_width=400))
    clock = ManualClock(50.0)
    recording = StreamRecording(hub, tmp_path / "stream.raw", encoding="h264", clock=clock)
    assert recording.kind == "frames" and await recording.start() == 50.0
    for _ in range(20):
        await asyncio.sleep(0)
    clock.now += 2
    await recording.stop()
    assert [(kind, data) for kind, _time, data in frames(recording.raw)] == [(1, KEY_FRAME), (1, FRAME)]
    assert struct.calcsize(">BdI") == FRAME_HEADER.size
    await hub.close()


async def test_a_jpeg_stream_recording_and_one_that_took_nothing(tmp_path: Path) -> None:
    engine = FakeEngine()
    hub = FrameHub(engine, SCREEN, StreamSettings(fps=30, quality=70, max_width=400))
    recording = StreamRecording(hub, tmp_path / "jpeg.raw", encoding="jpeg")
    await recording.start()
    for _ in range(20):
        await asyncio.sleep(0)
    await recording.stop()
    assert [kind for kind, _time, _data in frames(recording.raw)] == [2]
    engine.chunks = []
    idle = StreamRecording(hub, tmp_path / "idle.raw", encoding="h264")
    await idle.start()
    with pytest.raises(DeviceControlError, match="took no frames"):
        await idle.stop()
    with pytest.raises(ValueError, match="not a stream kind"):
        StreamRecording(hub, tmp_path / "x.raw", encoding="png")
    never = StreamRecording(hub, tmp_path / "never.raw", encoding="jpeg")
    with pytest.raises(DeviceControlError, match="took no frames"):
        await never.stop()
    await hub.close()


async def test_a_tool_that_ends_before_saying_anything_says_there_was_no_reason(tmp_path: Path) -> None:
    process = FakeProcess(5002)
    process.finish(1)

    async def begin(path: Path, log: Path) -> FakeProcess:
        return process

    recording = ToolRecording(begin, tmp_path / "rec.mp4", tmp_path / "missing.log", started="Recording started")
    with pytest.raises(DeviceControlError, match="did not start: no reason"):
        await recording.start()
