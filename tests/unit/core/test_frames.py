# SPDX-License-Identifier: Apache-2.0
"""The frame hub: one source per kind for every viewer, a still screen silent, H.264 from a sync point, trouble said."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Crop, Screen, Shot
from sim_mirror.core import frames as frames_module
from sim_mirror.core.frames import (
    KEY_FRAME_S,
    LINGER_S,
    RETRY_S,
    Frame,
    FrameHub,
    KeyFrameQueue,
    LatestFrame,
    StreamSettings,
    is_sync_point,
    nal_types,
)

SCREEN = Screen(width_px=1206, height_px=2622, width_pt=402, height_pt=874, scale=3.0)
SETTINGS = StreamSettings(fps=30, quality=75, max_width=600)
SPS = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps"
IDR = b"\x00\x00\x00\x01\x65idr"
DELTA = b"\x00\x00\x01\x41p"
A, B = Shot(b"jpeg-a", 600, 1304), Shot(b"jpeg-b", 600, 1304)


class FakeSource:
    def __init__(self, shots: Any = (A,), chunks: Any = (SPS, IDR, DELTA), errors: Any = ()) -> None:
        self.shots = list(shots)
        self.chunks = list(chunks)
        self.errors = list(errors)
        self.screenshots: list[tuple[int, int]] = []
        self.streams: list[dict[str, Any]] = []

    async def describe(self) -> Screen:
        return SCREEN

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.screenshots.append((max_width, quality))
        if self.errors:
            raise self.errors.pop(0)
        shot: Shot = self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]
        return shot

    async def _stream(self, **kwargs: Any) -> AsyncIterator[bytes]:
        self.streams.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        for chunk in self.chunks:
            yield chunk
        await asyncio.sleep(3600)

    def h264(self, **kwargs: Any) -> AsyncIterator[bytes]:
        return self._stream(**kwargs)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 4))
        self.now += seconds
        await asyncio.sleep(0)


async def until(predicate: Callable[[], object], tries: int = 500) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("never happened")


def hub_for(source: Any) -> tuple[FrameHub, Clock, list[str | None]]:
    clock = Clock()
    troubles: list[str | None] = []
    hub = FrameHub(source, SCREEN, SETTINGS, on_trouble=troubles.append, clock=clock, sleep=clock.sleep)
    return hub, clock, troubles


def test_a_chunk_is_a_sync_point_when_it_carries_a_sequence_parameter_set() -> None:
    assert nal_types(SPS) == {7, 8} and nal_types(IDR) == {5} and nal_types(DELTA) == {1} and nal_types(b"") == set()
    assert is_sync_point(SPS) and not is_sync_point(IDR) and not is_sync_point(DELTA)


async def test_a_jpeg_viewer_holds_only_the_newest_frame() -> None:
    viewer = LatestFrame()
    viewer.offer(Frame("jpeg", b"1"))
    viewer.offer(Frame("jpeg", b"2"))
    frame = await viewer.next()
    assert frame is not None and frame.data == b"2"
    waiting = asyncio.ensure_future(viewer.next())
    await asyncio.sleep(0)
    viewer.close()
    assert await waiting is None


async def test_an_h264_viewer_starts_at_a_sync_point_and_resyncs_when_it_falls_behind() -> None:
    viewer = KeyFrameQueue(limit=3)
    for chunk in (IDR, DELTA, SPS, IDR, DELTA):
        viewer.offer(Frame("h264", chunk))
    assert [(await viewer.next()).data for _ in range(3)] == [SPS, IDR, DELTA]  # type: ignore[union-attr]
    for chunk in (SPS, DELTA, DELTA, DELTA, IDR, SPS):
        viewer.offer(Frame("h264", chunk))
    assert (await viewer.next()).data == SPS  # type: ignore[union-attr]
    viewer.close()
    assert await viewer.next() is None


async def test_one_jpeg_source_feeds_every_viewer_and_a_still_screen_is_sent_once() -> None:
    source = FakeSource(shots=[A, A, A, B, B])
    hub, clock, _troubles = hub_for(source)
    first = hub.subscribe("jpeg")
    assert hub.running("jpeg") and hub.viewers == 1
    assert (await first.next()).data == b"jpeg-a"  # type: ignore[union-attr]
    second = hub.subscribe("jpeg")
    assert (await second.next()).data == b"jpeg-a"  # type: ignore[union-attr]
    assert (await first.next()).data == b"jpeg-b"  # type: ignore[union-attr]
    assert source.screenshots[0] == (600, 75) and hub.viewers == 2
    assert clock.slept and all(0 < seconds <= 1 / 30 for seconds in clock.slept)
    await hub.close()
    assert hub.viewers == 0 and await first.next() is None


async def test_a_source_that_fails_says_why_backs_off_and_says_when_it_is_well_again() -> None:
    source = FakeSource(errors=[ConnectorError("companion gone"), ConnectorError("companion gone")])
    hub, clock, troubles = hub_for(source)
    viewer = hub.subscribe("jpeg")
    assert (await viewer.next()).data == b"jpeg-a"  # type: ignore[union-attr]
    assert troubles == ["companion gone", "companion gone", None]
    assert clock.slept[:2] == [RETRY_S[0], RETRY_S[1]]
    await hub.close()


async def test_the_source_stops_a_while_after_the_last_viewer_leaves_unless_one_comes_back() -> None:
    hub, clock, _troubles = hub_for(FakeSource())
    viewer = hub.subscribe("jpeg")
    await viewer.next()
    hub.unsubscribe(viewer)
    returning = hub.subscribe("jpeg")
    await until(lambda: clock.now > LINGER_S)
    assert hub.running("jpeg")
    hub.unsubscribe(returning)
    hub.unsubscribe(returning)
    await until(lambda: not hub.running("jpeg"))
    assert LINGER_S in clock.slept
    await hub.close()


async def test_an_h264_viewer_gets_the_stream_from_its_sync_point_at_the_width_asked_for() -> None:
    source = FakeSource(chunks=[IDR, SPS, IDR, DELTA])
    hub, _clock, _troubles = hub_for(source)
    viewer = hub.subscribe("h264")
    assert [(await viewer.next()).data for _ in range(3)] == [SPS, IDR, DELTA]  # type: ignore[union-attr]
    stream = source.streams[0]
    assert stream["fps"] == 30 and stream["key_frame_s"] == KEY_FRAME_S
    assert stream["scale"] == pytest.approx(600 / 1206) and stream["bitrate"] >= 250_000
    await hub.close()


async def test_an_h264_stream_that_ends_or_fails_is_trouble_and_is_started_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frames_module, "RETRY_S", (0.5,))
    source = FakeSource(chunks=[SPS], errors=[ConnectorError("-12902")])

    async def short(**kwargs: Any) -> AsyncIterator[bytes]:
        source.streams.append(kwargs)
        if source.errors:
            raise source.errors.pop(0)
        yield SPS

    source.h264 = short  # type: ignore[method-assign]
    hub, clock, troubles = hub_for(source)
    viewer = hub.subscribe("h264")
    assert (await viewer.next()).data == SPS  # type: ignore[union-attr]
    await until(lambda: "the video stream ended" in troubles)
    assert troubles[:2] == ["-12902", None] and 0.5 in clock.slept
    await hub.close()


async def test_new_settings_restart_what_is_streaming_and_the_same_ones_do_not() -> None:
    source = FakeSource()
    hub, _clock, _troubles = hub_for(source)
    viewer = hub.subscribe("jpeg")
    await viewer.next()
    hub.reconfigure(SETTINGS)
    assert hub.settings is SETTINGS
    hub.reconfigure(StreamSettings(fps=10, quality=50, max_width=400))
    await until(lambda: source.screenshots[-1] == (400, 50))
    await hub.close()


async def test_a_new_source_takes_over_the_streams_and_viewers_stay_subscribed() -> None:
    old, new = FakeSource(shots=(A,)), FakeSource(shots=(B,), chunks=(SPS, DELTA))
    hub, _clock, _troubles = hub_for(old)
    jpeg, h264 = hub.subscribe("jpeg"), hub.subscribe("h264")
    assert (await jpeg.next()).data == A.jpeg and (await h264.next()).data == SPS  # type: ignore[union-attr]
    larger = Screen(width_px=1320, height_px=2868, width_pt=440, height_pt=956, scale=3.0)
    hub.rebind(new, larger)
    assert (await jpeg.next()).data == B.jpeg  # type: ignore[union-attr]
    await until(lambda: new.streams)
    assert hub.h264_scale() == pytest.approx(SETTINGS.max_width / 1320) and hub.viewers == 2
    await hub.close()


async def test_a_second_h264_viewer_shares_the_stream_and_it_winds_down_when_both_leave() -> None:
    source = FakeSource(chunks=[SPS, DELTA])
    hub, _clock, _troubles = hub_for(source)
    first, second = hub.subscribe("h264"), hub.subscribe("h264")
    assert (await first.next()).data == SPS and (await second.next()).data == SPS  # type: ignore[union-attr]
    assert len(source.streams) == 1
    hub.unsubscribe(first)
    hub.unsubscribe(second)
    await until(lambda: not hub.running("h264"))
    await hub.close()


async def test_a_screenshot_slower_than_a_frame_is_followed_by_the_next_at_once() -> None:
    clock = Clock()

    class Slow(FakeSource):
        async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
            clock.now += 0.1
            await asyncio.sleep(0)
            return await super().screenshot(max_width=max_width, quality=quality)

    hub = FrameHub(Slow(shots=[A, B]), SCREEN, SETTINGS, clock=clock, sleep=clock.sleep)
    viewer = hub.subscribe("jpeg")
    assert (await viewer.next()).data == b"jpeg-a"  # type: ignore[union-attr]
    assert (await viewer.next()).data == b"jpeg-b"  # type: ignore[union-attr]
    assert clock.slept == []
    await hub.close()


def test_a_kind_that_is_not_one_is_refused_and_settings_come_from_config_within_the_connectors_limit() -> None:
    hub = FrameHub(FakeSource(), SCREEN, SETTINGS)
    with pytest.raises(ValueError, match="not a stream kind"):
        hub.subscribe("mjpeg")
    config = SimConfig.defaults().with_values(stream_fps=12, stream_quality=40, stream_max_width=700)
    assert StreamSettings.from_config(config) == StreamSettings(12, 40, 700)
    assert StreamSettings.from_config(config, fps_limit=4) == StreamSettings(4, 40, 700)
    assert FrameHub(FakeSource(), Screen(0, 0, 0, 0, 0), SETTINGS).h264_scale() == 1.0
    assert FrameHub(FakeSource(), SCREEN, StreamSettings(30, 75, 5000)).h264_scale() == 1.0
