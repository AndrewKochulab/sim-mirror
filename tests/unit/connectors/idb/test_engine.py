# SPDX-License-Identifier: Apache-2.0
"""The engine over idb_companion's protocol, against an in-process fake companion: no device, no subprocess."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError, StreamTerminatedError
from grpclib.testing import ChannelFor

from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.connectors.idb.engine import IdbEngine, to_proto
from sim_mirror.connectors.idb.proto import idb_pb2 as pb
from sim_mirror.connectors.idb.proto.idb_grpc import CompanionServiceBase
from sim_mirror.testing.fakes import BOOTED_UDID, fixture

H = pb.HIDEvent


class FakeCompanion(CompanionServiceBase):
    """Answers the five RPCs the engine uses the way idb_companion 1.5 does."""

    def __init__(self) -> None:
        self.fail: GRPCError | None = None
        self.image_format = "jpeg"
        self.ax_json = fixture("ax-settings-interactable.json")
        self.screenshots: list[Any] = []
        self.ax_requests: list[Any] = []
        self.hid_events: list[Any] = []
        self.video_start: Any = None
        self.video_cancelled = False

    async def describe(self, stream: Any) -> None:
        await stream.recv_message()
        if self.fail:
            raise self.fail
        dims = pb.ScreenDimensions(width=1206, height=2622, density=3.0, width_points=402, height_points=874)
        await stream.send_message(
            pb.TargetDescriptionResponse(
                target_description=pb.TargetDescription(udid=BOOTED_UDID, screen_dimensions=dims)
            )
        )

    async def screenshot(self, stream: Any) -> None:
        self.screenshots.append(await stream.recv_message())
        await stream.send_message(
            pb.ScreenshotResponse(
                image_data=b"\xff\xd8jpeg\xff\xd9",
                image_format=self.image_format,
                destination=pb.ScreenshotResponse.Size(width=400, height=870),
            )
        )

    async def accessibility_info(self, stream: Any) -> None:
        self.ax_requests.append(await stream.recv_message())
        await stream.send_message(pb.AccessibilityInfoResponse(json=self.ax_json))

    async def hid(self, stream: Any) -> None:
        async for event in stream:
            self.hid_events.append(event)
        await stream.send_message(pb.HIDResponse())

    async def video_stream(self, stream: Any) -> None:
        self.video_start = await stream.recv_message()
        try:
            await stream.send_message(pb.VideoStreamResponse(log_output=b"starting"))
            for chunk in (b"\x00\x00\x00\x01\x67sps", b"", b"\x00\x00\x00\x01\x65idr"):
                await stream.send_message(pb.VideoStreamResponse(payload=pb.Payload(data=chunk)))
            if self.fail:
                raise self.fail
        except BaseException:
            self.video_cancelled = True
            raise


FakeCompanion.__abstractmethods__ = frozenset()


@pytest.fixture
async def rig() -> AsyncIterator[tuple[FakeCompanion, IdbEngine]]:
    companion = FakeCompanion()
    async with ChannelFor([companion]) as channel:
        yield companion, IdbEngine(channel)


async def test_describe_gives_the_screen_in_pixels_and_points(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    _companion, engine = rig
    assert await engine.describe() == Screen(width_px=1206, height_px=2622, width_pt=402, height_pt=874, scale=3.0)


async def test_a_refused_call_is_an_error_a_person_can_read(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig
    companion.fail = GRPCError(Status.UNAVAILABLE, "device is not booted")
    with pytest.raises(ConnectorError, match="describing the device failed: device is not booted"):
        await engine.describe()
    companion.fail = GRPCError(Status.UNAVAILABLE)
    with pytest.raises(ConnectorError, match="describing the device failed: UNAVAILABLE"):
        await engine.describe()


async def test_a_screenshot_is_scaled_and_encoded_by_the_companion(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig
    assert await engine.screenshot(max_width=400, quality=70) == Shot(b"\xff\xd8jpeg\xff\xd9", 400, 870)
    plain = companion.screenshots[-1]
    assert (plain.format, plain.fit.max_width, plain.HasField("crop"), plain.unit) == (
        pb.ScreenshotRequest.JPEG,
        400,
        False,
        pb.ScreenshotRequest.PIXELS,
    )
    assert plain.compression_quality == pytest.approx(0.7)
    await engine.screenshot(max_width=800, quality=90, crop=Crop(10, 20, 100, 50))
    cropped = companion.screenshots[-1]
    assert (cropped.crop.x, cropped.crop.y, cropped.crop.width, cropped.crop.height) == (10, 20, 100, 50)
    assert cropped.unit == pb.ScreenshotRequest.POINTS


async def test_a_companion_too_old_to_scale_a_screenshot_says_to_upgrade(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig
    companion.image_format = ""
    with pytest.raises(ConnectorError, match="brew upgrade idb-companion"):
        await engine.screenshot(max_width=400, quality=70)


async def test_the_video_stream_yields_only_frames_and_asks_for_what_was_set(
    rig: tuple[FakeCompanion, IdbEngine],
) -> None:
    companion, engine = rig
    chunks = [chunk async for chunk in engine.h264(fps=60, scale=0.5, key_frame_s=1.0, bitrate=1_500_000)]
    assert chunks == [b"\x00\x00\x00\x01\x67sps", b"\x00\x00\x00\x01\x65idr"]
    start = companion.video_start.start
    assert (start.format, start.fps, start.scale_factor, start.key_frame_rate, start.avg_bitrate) == (
        pb.VideoStreamRequest.H264,
        60,
        0.5,
        1.0,
        1_500_000,
    )


async def test_closing_the_stream_early_ends_it_at_the_companion(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    _companion, engine = rig
    frames = engine.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=0)
    assert await frames.__anext__() == b"\x00\x00\x00\x01\x67sps"
    await frames.aclose()  # type: ignore[attr-defined]


async def test_a_stream_whose_connection_is_already_gone_ends_quietly() -> None:
    """Found against a real companion: a reader stopped, the channel closed, then the stream was closed."""

    class Gone:
        cancelled = False

        async def __aenter__(self) -> Gone:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def send_message(self, message: object) -> None:
            return None

        async def recv_message(self) -> Any:
            return pb.VideoStreamResponse(payload=pb.Payload(data=b"frame"))

        async def cancel(self) -> None:
            self.cancelled = True
            raise StreamTerminatedError("Connection lost")

    gone = Gone()
    engine = IdbEngine.at("/nonexistent/companion.sock")
    engine._stub = type("Stub", (), {"video_stream": type("Method", (), {"open": lambda self: gone})()})()
    frames = engine.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=0)
    assert await frames.__anext__() == b"frame"
    await frames.aclose()  # type: ignore[attr-defined]
    assert gone.cancelled
    await engine.close()


async def test_a_stream_the_companion_breaks_is_an_error(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig
    companion.fail = GRPCError(Status.INTERNAL, "Failed to start Compression Session -12902")
    with pytest.raises(ConnectorError, match="the video stream failed: Failed to start Compression Session"):
        _ = [chunk async for chunk in engine.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=0)]


async def test_input_goes_down_one_stream_in_order(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig

    async def events() -> AsyncIterator[HidEvent]:
        yield HidEvent.touch("down", 10, 20)
        yield HidEvent.touch("up", 10, 20)
        yield HidEvent.press("home", "down")
        yield HidEvent.key(40, "up")

    await engine.hid(events())
    assert companion.hid_events == [
        to_proto(HidEvent.touch("down", 10, 20)),
        to_proto(HidEvent.touch("up", 10, 20)),
        to_proto(HidEvent.press("home", "down")),
        to_proto(HidEvent.key(40, "up")),
    ]


def test_every_kind_of_event_is_spelled_as_the_protocol_does() -> None:
    touch = to_proto(HidEvent.touch("down", 1.5, 2.5))
    assert touch.press.direction == H.DOWN
    assert (touch.press.action.touch.point.x, touch.press.action.touch.point.y) == (1.5, 2.5)
    buttons = [to_proto(HidEvent.press(name, "up")).press.action.button.button for name in
               ("home", "lock", "side", "siri", "apple_pay")]  # fmt: skip
    assert buttons == [H.HOME, H.LOCK, H.SIDE_BUTTON, H.SIRI, H.APPLE_PAY]
    key = to_proto(HidEvent.key(227, "up"))
    assert key.press.action.key.keycode == 227 and key.press.direction == H.UP


async def test_the_screen_is_read_in_the_consolidated_form(rig: tuple[FakeCompanion, IdbEngine]) -> None:
    companion, engine = rig
    document = await engine.accessibility()
    assert {"elements", "screen", "truncated"} <= set(document)
    request = companion.ax_requests[-1]
    assert (request.format, request.filter) == (
        pb.AccessibilityInfoRequest.COMPLETE,
        pb.AccessibilityInfoRequest.FILTER_INTERACTABLE,
    )


@pytest.mark.parametrize(
    ("answer", "message"),
    [(json.dumps([{"type": "Application"}]), "brew upgrade idb-companion"), ("{not json", "not JSON")],
)
async def test_a_screen_read_that_is_not_the_consolidated_document_is_refused(
    rig: tuple[FakeCompanion, IdbEngine], answer: str, message: str
) -> None:
    companion, engine = rig
    companion.ax_json = answer
    with pytest.raises(ConnectorError, match=message):
        await engine.accessibility()


async def test_a_companion_that_is_not_there_did_not_answer(tmp_path: Any) -> None:
    engine = IdbEngine.at(str(tmp_path / "gone.sock"))
    with pytest.raises(ConnectorError, match="did not answer"):
        await engine.describe()
    await engine.close()
