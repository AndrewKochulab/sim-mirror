# SPDX-License-Identifier: Apache-2.0
"""A booted device through idb_companion's gRPC socket: its screen, its input and what is on it.

One `IdbEngine` per companion, which is one per booted device. Each call maps to one RPC of idb's own protocol
(`proto/`): ``describe``; ``screenshot``, with the companion scaling and encoding the JPEG; ``video_stream`` as H.264;
``hid`` as one stream per gesture; ``accessibility_info`` in its consolidated form.

A call that fails -- the companion gone, the device shut down under it, an RPC it refuses -- raises `ConnectorError`
with something a person can act on, never a grpclib exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from typing import Any

from grpclib.client import Channel
from grpclib.exceptions import GRPCError, StreamTerminatedError

from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.connectors.idb.proto import idb_pb2 as pb
from sim_mirror.connectors.idb.proto.idb_grpc import CompanionServiceStub

_HID = pb.HIDEvent
_BUTTONS = {
    "home": _HID.HOME,
    "lock": _HID.LOCK,
    "side": _HID.SIDE_BUTTON,
    "siri": _HID.SIRI,
    "apple_pay": _HID.APPLE_PAY,
}
_PHASES = {"down": _HID.DOWN, "up": _HID.UP}
_AX = pb.AccessibilityInfoRequest
_SHOT = pb.ScreenshotRequest

DESCRIBE_TIMEOUT_S = 5.0
SCREENSHOT_TIMEOUT_S = 5.0
ACCESSIBILITY_TIMEOUT_S = 10.0

UPGRADE = "brew upgrade idb-companion"


@contextlib.contextmanager
def _failures(what: str) -> Iterator[None]:
    try:
        yield
    except GRPCError as exc:
        raise ConnectorError(f"{what} failed: {exc.message or exc.status.name}") from exc
    except (StreamTerminatedError, OSError, asyncio.TimeoutError) as exc:
        detail = str(exc) or type(exc).__name__
        raise ConnectorError(f"{what} failed: the simulator companion did not answer ({detail})") from exc


def to_proto(event: HidEvent) -> Any:
    """One input event as the companion's protocol spells it."""
    if event.kind == "touch":
        action = _HID.HIDPressAction(touch=_HID.HIDTouch(point=pb.Point(x=event.x, y=event.y)))
    elif event.kind == "button":
        action = _HID.HIDPressAction(button=_HID.HIDButton(button=_BUTTONS[event.button]))
    else:
        action = _HID.HIDPressAction(key=_HID.HIDKey(keycode=event.code))
    return _HID(press=_HID.HIDPress(action=action, direction=_PHASES[event.phase]))


class IdbEngine:
    """A booted device, through its companion."""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel
        self._stub = CompanionServiceStub(channel)

    @classmethod
    def at(cls, socket_path: str) -> IdbEngine:
        """The engine for the companion serving on this unix socket."""
        return cls(Channel(path=socket_path))

    async def describe(self) -> Screen:
        with _failures("describing the device"):
            response = await self._stub.describe(pb.TargetDescriptionRequest(), timeout=DESCRIBE_TIMEOUT_S)
        dimensions = response.target_description.screen_dimensions
        return Screen(
            width_px=dimensions.width,
            height_px=dimensions.height,
            width_pt=dimensions.width_points,
            height_pt=dimensions.height_points,
            scale=dimensions.density,
        )

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        request = _SHOT(format=_SHOT.JPEG, compression_quality=quality / 100, fit=_SHOT.Fit(max_width=max_width))
        if crop is not None:
            request.crop.CopyFrom(_SHOT.Rect(x=crop.x, y=crop.y, width=crop.width, height=crop.height))
            request.unit = _SHOT.POINTS
        with _failures("taking a screenshot"):
            response = await self._stub.screenshot(request, timeout=SCREENSHOT_TIMEOUT_S)
        if response.image_format != "jpeg":
            # A companion that predates scaled screenshots ignores every field and sends a full PNG.
            raise ConnectorError(f"this idb_companion cannot scale screenshots; {UPGRADE}")
        return Shot(jpeg=response.image_data, width=response.destination.width, height=response.destination.height)

    async def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        start = pb.VideoStreamRequest.Start(
            fps=fps, format=pb.VideoStreamRequest.H264, scale_factor=scale, key_frame_rate=key_frame_s,
            avg_bitrate=bitrate,
        )  # fmt: skip
        with _failures("the video stream"):
            async with self._stub.video_stream.open() as stream:
                await stream.send_message(pb.VideoStreamRequest(start=start))
                try:
                    while (message := await stream.recv_message()) is not None:
                        if message.WhichOneof("output") == "payload" and message.payload.data:
                            yield message.payload.data
                finally:
                    await self._cancel_quietly(stream)

    @staticmethod
    async def _cancel_quietly(stream: Any) -> None:
        """End a stream. One whose connection is already gone -- the viewer left, the engine closed -- is no failure."""
        with contextlib.suppress(StreamTerminatedError, OSError):
            await stream.cancel()

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        with _failures("sending input"):
            async with self._stub.hid.open() as stream:
                async for event in events:
                    await stream.send_message(to_proto(event))
                await stream.end()
                await stream.recv_message()

    async def accessibility(self) -> dict[str, Any]:
        request = _AX(format=_AX.COMPLETE, filter=_AX.FILTER_INTERACTABLE)
        with _failures("reading the screen"):
            response = await self._stub.accessibility_info(request, timeout=ACCESSIBILITY_TIMEOUT_S)
        try:
            document = json.loads(response.json)
        except ValueError as exc:
            raise ConnectorError(
                "reading the screen failed: the companion answered with something that is not JSON"
            ) from exc
        if not isinstance(document, dict):
            # An older companion does not know the consolidated format and answers the legacy list.
            raise ConnectorError(f"this idb_companion cannot describe the screen in the form needed; {UPGRADE}")
        return document

    async def close(self) -> None:
        self._channel.close()
