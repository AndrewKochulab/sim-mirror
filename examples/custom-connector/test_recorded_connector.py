# SPDX-License-Identifier: Apache-2.0
"""The recorded connector keeps SimMirror's connector contract, and says why when it cannot be used."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from recorded_connector import FRAMES_ENV, NAME, RecordedConnector, create, jpeg_size, load_frames

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.testing.contract import check_connector
from sim_mirror.testing.fakes import tiny_jpeg

CONFIG = SimConfig.defaults()


def recording(folder: Path) -> Path:
    (folder / "002.jpg").write_bytes(tiny_jpeg(1206, 2622, b"second"))
    (folder / "001.jpeg").write_bytes(tiny_jpeg(1206, 2622, b"first"))
    (folder / "notes.txt").write_text("not a frame")
    (folder / "broken.jpg").write_bytes(b"not a jpeg")
    return folder


async def test_a_folder_of_frames_keeps_the_contract(tmp_path: Path) -> None:
    connector = RecordedConnector({FRAMES_ENV: str(recording(tmp_path))})
    assert await check_connector(connector, CONFIG, "recorded-device") == []


async def test_without_frames_it_says_why_and_attaches_to_nothing(tmp_path: Path) -> None:
    for env in ({}, {FRAMES_ENV: str(tmp_path)}, {FRAMES_ENV: str(tmp_path / "missing")}):
        connector = RecordedConnector(env)
        report = await connector.probe(CONFIG)
        assert not report.available and report.reasons
        assert await check_connector(connector, CONFIG, "recorded-device") == []
        with pytest.raises(ConnectorUnavailable, match=FRAMES_ENV):
            await connector.attach("recorded-device", CONFIG)


async def test_frames_play_in_name_order_and_start_over(tmp_path: Path) -> None:
    session = await RecordedConnector({FRAMES_ENV: str(recording(tmp_path))}).attach("recorded-device", CONFIG)
    screen = await session.screen.describe()
    assert (screen.width_px, screen.height_px, screen.width_pt, screen.height_pt) == (1206, 2622, 402, 874)
    shots = [await session.screen.screenshot(max_width=400, quality=70) for _ in range(3)]
    first, second = tiny_jpeg(1206, 2622, b"first"), tiny_jpeg(1206, 2622, b"second")
    assert [shot.jpeg for shot in shots] == [first, second, first]
    assert (shots[0].width, shots[0].height) == (1206, 2622)
    assert session.input is None and session.reader is None and session.fps_limit == 4
    with pytest.raises(ConnectorUnavailable, match=r"H\.264"):
        session.screen.h264(fps=30, scale=1.0, key_frame_s=2.0, bitrate=1_000_000)


def test_a_jpeg_is_read_by_its_frame_header_and_anything_else_is_not_one(tmp_path: Path) -> None:
    assert jpeg_size(tiny_jpeg(640, 480)) == (640, 480)
    app_segment = b"\xff\xd8\xff\xe0\x00\x04ab" + tiny_jpeg(20, 10)[2:]
    assert jpeg_size(app_segment) == (20, 10)
    assert jpeg_size(b"GIF89a") is None
    assert jpeg_size(b"\xff\xd8\xff\xe0\x00") is None
    assert load_frames(tmp_path / "missing") == []


def test_the_entry_point_makes_the_connector() -> None:
    context: Any = object()
    assert create(context).name == NAME
