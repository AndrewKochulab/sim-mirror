# SPDX-License-Identifier: Apache-2.0
"""An Android screen's size, which way it is turned, and its screenshots turned back to the portrait screen."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator

import pytest
from PIL import Image

from sim_mirror.connectors.android.geometry import (
    ROTATIONS,
    Rotation,
    on_display,
    orientation_of,
    portrait_jpeg,
    quarters_of,
    screen_of,
)
from sim_mirror.connectors.android.screen import NO_STREAM, AdbScreen
from sim_mirror.connectors.base import ConnectorError, Crop, Screen
from sim_mirror.testing.adb import FakeAdbServer, small_jpeg

PIXEL = Screen(1080, 2424, 411, 923, 2.625)


def picture(width: int, height: int, *, fmt: str = "JPEG") -> bytes:
    """A picture whose top-left quarter is red, so how it was turned shows."""
    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, width // 2, height // 2))
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def red_corner(jpeg: bytes) -> str:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    corners = {
        "top left": (2, 2),
        "top right": (image.width - 3, 2),
        "bottom left": (2, image.height - 3),
        "bottom right": (image.width - 3, image.height - 3),
    }
    return next(name for name, point in corners.items() if image.getpixel(point)[0] > 200)  # type: ignore[index]


def test_the_screen_is_its_pixels_and_density_independent_points_an_override_first() -> None:
    assert screen_of("Physical size: 1080x2424\n", "Physical density: 420\n") == PIXEL
    overridden = screen_of(
        "Physical size: 1080x2424\nOverride size: 720x1616\n", "Physical density: 420\nOverride density: 320\n"
    )
    assert overridden == Screen(720, 1616, 360, 808, 2.0)
    # A tablet whose natural orientation is landscape is still described portrait, as every screen is.
    assert screen_of("Physical size: 2560x1600", "Physical density: 320") == Screen(1600, 2560, 800, 1280, 2.0)
    with pytest.raises(ConnectorError, match="did not say its screen's size"):
        screen_of("error: closed", "")


def test_each_quarter_turn_is_a_way_an_interface_lies_on_the_portrait_screen() -> None:
    assert ROTATIONS == ("portrait", "landscape_right", "portrait_upside_down", "landscape_left")
    assert [orientation_of(quarters) for quarters in (0, 1, 2, 3, 4)] == [*ROTATIONS, "portrait"]
    assert quarters_of("mOverrideDisplayInfo\n  mCurrentOrientation=3\n") == 3
    assert quarters_of("nothing said") == 0


@pytest.mark.parametrize(
    ("quarters", "top_left", "bottom_right"),
    [
        (0, (0, 0), (1080, 2424)),
        # Turned a quarter, the portrait screen's top left is the display's bottom left.
        (1, (0, 1080), (2424, 0)),
        (2, (1080, 2424), (0, 0)),
        (3, (2424, 0), (0, 1080)),
    ],
)
def test_a_point_of_the_portrait_screen_is_turned_onto_the_display(
    quarters: int, top_left: tuple[int, int], bottom_right: tuple[int, int]
) -> None:
    assert on_display(PIXEL, quarters, 0, 0) == (
        pytest.approx(top_left[0], abs=3),
        pytest.approx(top_left[1], abs=3),
    )
    assert on_display(PIXEL, quarters, 411, 923) == (
        pytest.approx(bottom_right[0], abs=3),
        pytest.approx(bottom_right[1], abs=3),
    )


@pytest.mark.parametrize(
    ("quarters", "corner"),
    [(0, "top left"), (1, "top right"), (2, "bottom right"), (3, "bottom left")],
)
def test_a_screenshot_of_the_turned_display_is_turned_back_to_the_portrait_screen(quarters: int, corner: str) -> None:
    shape = (200, 100) if quarters % 2 else (100, 200)
    jpeg, width, height = portrait_jpeg(picture(*shape), quarters, max_width=1000, quality=90, crop=None)
    assert (width, height) == (100, 200) and red_corner(jpeg) == corner


def test_a_screenshot_is_cropped_and_shrunk_and_a_png_is_read_as_well() -> None:
    jpeg, width, height = portrait_jpeg(picture(100, 200, fmt="PNG"), 0, max_width=25, quality=80, crop=(0, 0, 50, 100))
    assert (width, height) == (25, 50) and jpeg.startswith(b"\xff\xd8") and red_corner(jpeg) == "top left"
    with pytest.raises(ConnectorError, match="is not a picture"):
        portrait_jpeg(b"not a picture", 0, max_width=100, quality=80, crop=None)


async def test_which_way_the_display_is_turned_is_asked_again_only_after_a_while_or_once_it_is_turned() -> None:
    asked: list[int] = []
    now = [0.0]

    async def ask() -> str:
        asked.append(1)
        return f"mCurrentOrientation={len(asked)}"

    rotation = Rotation(ask, clock=lambda: now[0])
    assert await rotation.quarters() == 1 and await rotation.quarters() == 1
    now[0] = 1.5
    assert await rotation.quarters() == 2
    rotation.forget()
    assert await rotation.quarters() == 3


@pytest.fixture
async def server() -> AsyncIterator[FakeAdbServer]:
    async with FakeAdbServer() as running:
        yield running.emulator()


def fixed(quarters: int) -> Rotation:
    async def ask() -> str:
        return f"mCurrentOrientation={quarters}"

    return Rotation(ask)


async def test_a_screenshot_is_the_devices_own_jpeg_shrunk_and_cropped(server: FakeAdbServer) -> None:
    screen = AdbScreen(server.adb(), "emulator-5554", PIXEL, fixed(0))
    assert await screen.describe() == PIXEL
    shot = await screen.screenshot(max_width=50, quality=70)
    assert (shot.width, shot.height) == (50, 112) and shot.jpeg.startswith(b"\xff\xd8")
    cropped = await screen.screenshot(max_width=400, quality=70, crop=Crop(0, 0, 20.0, 20.0))
    assert (cropped.width, cropped.height) == (52, 52)


async def test_an_android_without_jpeg_screenshots_is_read_as_png_from_then_on(server: FakeAdbServer) -> None:
    server.on("screencap -j", out="usage: screencap [-hp] [-d display-id] [FILENAME]\n")
    server.on("screencap -p", out=picture(108, 242, fmt="PNG"))
    screen = AdbScreen(server.adb(), "emulator-5554", PIXEL, fixed(0))
    assert (await screen.screenshot(max_width=108, quality=70)).width == 108
    assert (await screen.screenshot(max_width=108, quality=70)).width == 108
    assert [command.split()[1] for command in server.commands() if command.startswith("screencap")] == [
        "-j",
        "-p",
        "-p",
    ]


async def test_a_screen_that_cannot_be_read_says_so_and_streams_nothing_without_scrcpy(server: FakeAdbServer) -> None:
    screen = AdbScreen(server.adb(), "emulator-9", PIXEL, fixed(0))
    with pytest.raises(ConnectorError, match="could not be read"):
        await screen.screenshot(max_width=100, quality=70)
    with pytest.raises(ConnectorError, match="set scrcpy up"):
        await anext(screen.h264(fps=30, scale=1, key_frame_s=1, bitrate=1))
    assert "H.264" in NO_STREAM and small_jpeg().startswith(b"\xff\xd8")
