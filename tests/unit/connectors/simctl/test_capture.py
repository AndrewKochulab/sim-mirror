# SPDX-License-Identifier: Apache-2.0
"""The simctl screen: sizes read from the JPEG itself, points estimated from the screen's shape, shrunk with sips
when asked, and what it cannot do refused plainly."""

from __future__ import annotations

import pytest

from sim_mirror.connectors.base import ConnectorError, Crop, Screen, Shot
from sim_mirror.connectors.simctl.capture import SimctlScreen, estimated_scale, jpeg_size
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, FakeXcrun, tiny_jpeg

PHONE = tiny_jpeg(1206, 2622)


def test_a_jpegs_size_is_read_from_its_frame_header_past_other_segments() -> None:
    assert jpeg_size(PHONE) == (1206, 2622)
    app0 = b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9
    padded = b"\xff\xd8" + app0 + b"\xff\xff" + b"\xff\x01" + tiny_jpeg(640, 480)[2:]
    assert jpeg_size(padded) == (640, 480)
    progressive = PHONE.replace(b"\xff\xc0", b"\xff\xc2", 1)
    assert jpeg_size(progressive) == (1206, 2622)


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n",
        b"\xff\xd8",
        b"\xff\xd8\x00\x00\x00\x00",
        b"\xff\xd8\xff\xc0\x00\x11\x08\x0a",
        b"\xff\xd8\xff\xc4\x00\x02",
    ],
)
def test_what_is_not_a_whole_jpeg_has_no_size(data: bytes) -> None:
    assert jpeg_size(data) is None


def test_points_are_estimated_from_the_shape() -> None:
    assert estimated_scale(1206, 2622) == 3.0 and estimated_scale(2622, 1206) == 3.0
    assert estimated_scale(750, 1334) == 2.0 and estimated_scale(2064, 2752) == 2.0
    assert estimated_scale(0, 0) == 2.0


def screen(fake: FakeXcrun, resized: bytes | None = None) -> tuple[SimctlScreen, list[tuple[int, int]]]:
    asked: list[tuple[int, int]] = []

    async def resize(data: bytes, *, max_width: int, quality: int) -> bytes | None:
        asked.append((max_width, quality))
        return resized

    return SimctlScreen(Simctl(fake), BOOTED_UDID, resize=resize), asked


async def test_the_screen_is_described_from_a_screenshot() -> None:
    capture, _ = screen(FakeXcrun().on("simctl", "io", raw=PHONE))
    assert await capture.describe() == Screen(1206, 2622, 402, 874, 3.0)


async def test_a_screenshot_is_shrunk_only_when_it_is_wider_than_asked() -> None:
    small = tiny_jpeg(400, 870)
    capture, asked = screen(FakeXcrun().on("simctl", "io", raw=PHONE), resized=small)
    assert await capture.screenshot(max_width=2000, quality=70) == Shot(PHONE, 1206, 2622)
    assert asked == []
    assert await capture.screenshot(max_width=400, quality=70) == Shot(small, 400, 870)
    assert asked == [(400, 70)]


@pytest.mark.parametrize("resized", [None, b"not a jpeg"])
async def test_a_shrink_that_does_not_work_answers_the_whole_screenshot(resized: bytes | None) -> None:
    capture, _ = screen(FakeXcrun().on("simctl", "io", raw=PHONE), resized=resized)
    assert await capture.screenshot(max_width=400, quality=70) == Shot(PHONE, 1206, 2622)


async def test_a_screenshot_that_fails_or_is_not_a_jpeg_is_an_error() -> None:
    failing, _ = screen(FakeXcrun().on("simctl", "io", rc=1, err="No devices are booted."))
    with pytest.raises(ConnectorError, match="taking a screenshot failed: simctl io: No devices are booted"):
        await failing.describe()
    png, _ = screen(FakeXcrun().on("simctl", "io", raw=b"\x89PNG\r\n"))
    with pytest.raises(ConnectorError, match="not a JPEG"):
        await png.screenshot(max_width=400, quality=70)


async def test_what_simctl_cannot_do_is_refused_with_the_connector_that_can() -> None:
    capture, _ = screen(FakeXcrun().on("simctl", "io", raw=PHONE))
    with pytest.raises(ConnectorError, match="region needs the idb connector"):
        await capture.screenshot(max_width=400, quality=70, crop=Crop(0, 0, 10, 10))
    stream = capture.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=0)
    assert stream.__aiter__() is stream
    with pytest.raises(ConnectorError, match=r"H\.264 stream needs the idb connector"):
        await stream.__anext__()
