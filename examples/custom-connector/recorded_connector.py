# SPDX-License-Identifier: Apache-2.0
"""An example connector: a "device" that is a folder of JPEG screenshots, played one after another.

It has the whole shape of a connector and needs nothing installed. `probe` says whether it can be used here, and why not
when it cannot; `attach` hands over a `DeviceSession` whose roles match the capabilities it promised; `reap_orphans`
ends what an earlier run left behind (nothing, here). It is view-only -- no `InputSink`, no `ScreenReader` -- so
SimMirror offers neither input nor snapshots on a device it shows.

Installed, it is found through the ``sim_mirror.connectors`` entry point (see pyproject.toml). Point it at a folder with
``RECORDED_FRAMES_DIR`` and choose it with ``connectors.preferred = "recorded"`` in SimMirror's config.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import (
    Capability,
    ConnectorReport,
    ConnectorUnavailable,
    Crop,
    DeviceSession,
    Screen,
    Shot,
)
from sim_mirror.connectors.registry import ConnectorContext

NAME = "recorded"
FRAMES_ENV = "RECORDED_FRAMES_DIR"
CAPABILITIES = frozenset({Capability.SCREENSHOT, Capability.STREAM_JPEG})
#: Pixels a point: frames recorded from a 3x device.
SCALE = 3.0
#: The most frames a second the viewer is sent.
FPS_LIMIT = 4
#: Start-of-frame markers, which carry a JPEG's size.
_FRAME_MARKERS = frozenset({0xC0, 0xC1, 0xC2})


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """A JPEG's (width, height) from its frame header, or None for bytes that are not one."""
    if not data.startswith(b"\xff\xd8"):
        return None
    at = 2
    while at + 9 <= len(data) and data[at] == 0xFF:
        if data[at + 1] in _FRAME_MARKERS:
            return int.from_bytes(data[at + 7 : at + 9], "big"), int.from_bytes(data[at + 5 : at + 7], "big")
        at += 2 + int.from_bytes(data[at + 2 : at + 4], "big")
    return None


def load_frames(folder: Path) -> list[bytes]:
    """The folder's JPEGs, in name order; anything else in it is left out."""
    if not folder.is_dir():
        return []
    paths = sorted(path for path in folder.iterdir() if path.suffix.lower() in (".jpg", ".jpeg"))
    return [data for data in (path.read_bytes() for path in paths) if jpeg_size(data) is not None]


class RecordedScreen:
    """The screen of a recording: its frames, one after another, starting over at the end."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.shown = 0

    async def describe(self) -> Screen:
        width, height = jpeg_size(self.frames[0]) or (0, 0)
        return Screen(
            width_px=width,
            height_px=height,
            width_pt=round(width / SCALE),
            height_pt=round(height / SCALE),
            scale=SCALE,
        )

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        """The next frame as it was recorded: this example neither scales nor crops."""
        frame = self.frames[self.shown % len(self.frames)]
        self.shown += 1
        width, height = jpeg_size(frame) or (0, 0)
        return Shot(frame, width, height)

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        raise ConnectorUnavailable("a recording has no H.264 stream", 409)


class RecordedConnector:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    @property
    def name(self) -> str:
        return NAME

    async def _frames(self) -> tuple[str, list[bytes]]:
        folder = self._env.get(FRAMES_ENV, "")
        return folder, (await asyncio.to_thread(load_frames, Path(folder)) if folder else [])

    async def probe(self, config: SimConfig) -> ConnectorReport:
        folder, frames = await self._frames()
        if not folder:
            return ConnectorReport(NAME, False, reasons=(f"{FRAMES_ENV} names no folder of recorded frames",))
        if not frames:
            return ConnectorReport(NAME, False, reasons=(f"{folder} holds no JPEG frames",))
        return ConnectorReport(NAME, True, CAPABILITIES, {"frames": str(len(frames))})

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        _, frames = await self._frames()
        if not frames:
            raise ConnectorUnavailable(f"there are no recorded frames to show; set {FRAMES_ENV}", 409)
        return DeviceSession(
            connector=NAME, capabilities=CAPABILITIES, screen=RecordedScreen(frames), fps_limit=FPS_LIMIT
        )

    async def reap_orphans(self) -> int:
        """A recording starts no process, so it leaves none behind."""
        return 0


def create(context: ConnectorContext) -> RecordedConnector:
    """The entry point SimMirror calls, with what a connector may need from its host."""
    return RecordedConnector()
