# SPDX-License-Identifier: Apache-2.0
"""The screen as an agent sees it when an app is turned on it: points, regions, touches and screenshots."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from sim_mirror.connectors.base import Crop, HidEvent, Screen, Shot
from sim_mirror.perception.orientation import ORIENTATIONS, Interface, Orientation, orientation_of
from sim_mirror.testing.fakes import JPEG, SCREEN
from sim_mirror.testing.pictures import picture


def test_a_document_says_its_orientation_or_is_portrait() -> None:
    assert orientation_of("landscape_left") == "landscape_left"
    assert orientation_of(None) == orientation_of("sideways") == orientation_of(3) == "portrait"
    assert ORIENTATIONS == ("portrait", "portrait_upside_down", "landscape_left", "landscape_right")


@pytest.mark.parametrize(
    ("orientation", "size", "words", "where"),
    [
        # Measured on an iPhone 17 Pro simulator: the screen's (30, 90) is (90, 372) turned right, (784, 30) left.
        ("portrait", (402, 874), "", (30, 90)),
        ("landscape_right", (874, 402), "landscape", (90, 372)),
        ("landscape_left", (874, 402), "landscape", (784, 30)),
        ("portrait_upside_down", (402, 874), "upside down", (372, 784)),
    ],
)
def test_each_orientation_puts_the_interface_on_the_screen(
    orientation: Orientation, size: tuple[int, int], words: str, where: tuple[float, float]
) -> None:
    interface = Interface(SCREEN, orientation)
    assert (interface.screen.width_pt, interface.screen.height_pt) == size
    assert interface.screen.scale == SCREEN.scale and interface.words() == words
    assert interface.turned == (orientation != "portrait")
    assert interface.on_device(*where) == (30, 90)
    assert interface.on_interface(30, 90) == where


def test_a_region_of_a_turned_interface_is_the_region_of_the_screen_it_covers() -> None:
    assert Interface(SCREEN, "landscape_right").crop(Crop(814, 60, 60, 30)) == Crop(312, 814, 30, 60)
    assert Interface(SCREEN, "landscape_left").crop(Crop(0, 0, 100, 50)) == Crop(0, 774, 50, 100)
    assert Interface(SCREEN).crop(Crop(1, 2, 3, 4)) == Crop(1, 2, 3, 4)


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_a_region_of_the_screen_is_the_region_of_the_interface_it_covers(orientation: Orientation) -> None:
    interface = Interface(SCREEN, orientation)
    region = Crop(814, 60, 60, 30) if interface.sideways else Crop(60, 814, 30, 60)
    assert interface.region(interface.crop(region)) == region
    assert Interface(SCREEN, "landscape_right").region(Crop(312, 814, 30, 60)) == Crop(814, 60, 60, 30)


async def test_touches_in_the_interface_go_to_the_screen_and_buttons_as_they_are() -> None:
    async def given() -> object:
        for event in (HidEvent.touch("down", 844, 90), HidEvent.press("home", "down")):
            yield event

    sent = [event async for event in Interface(SCREEN, "landscape_right").events(given())]  # type: ignore[arg-type]
    assert sent == [HidEvent.touch("down", 312, 844), HidEvent.press("home", "down")]
    assert Interface(SCREEN).event(HidEvent.touch("up", 5, 6)) == HidEvent.touch("up", 5, 6)


def test_a_sideways_screenshot_is_asked_for_narrow_enough_to_be_no_wider_upright() -> None:
    sideways = Interface(SCREEN, "landscape_left")
    assert sideways.device_width(874, None) == 402
    assert sideways.device_width(800, Crop(0, 0, 100, 200)) == 400
    assert sideways.device_width(800, Crop(0, 0, 0, 200)) == 800
    assert Interface(SCREEN, "portrait_upside_down").device_width(800, None) == 800


def corner(jpeg: bytes) -> tuple[int, int, int]:
    """Where the picture's dark box is: its size, and whether it is at the top left."""
    with Image.open(BytesIO(jpeg)) as image:
        grey = image.convert("L")
        return grey.width, grey.height, int(grey.getpixel((5, 5)) < 128)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("orientation", "box"),
    [
        # The interface's top left corner is the screen's top right turned right, its bottom left turned left.
        ("landscape_right", (382, 0, 20, 20, 0)),
        ("landscape_left", (0, 854, 20, 20, 0)),
        ("portrait_upside_down", (382, 854, 20, 20, 0)),
    ],
)
async def test_a_screenshot_of_a_turned_interface_is_turned_upright(
    orientation: Orientation, box: tuple[int, int, int, int, int]
) -> None:
    shot = Shot(picture(402, 874, boxes=[box]), 402, 874)
    upright = await Interface(SCREEN, orientation).upright(shot)
    width, height, dark = corner(upright.jpeg)
    assert (upright.width, upright.height) == (width, height)
    assert dark == 1 and (width, height) == ((402, 874) if orientation == "portrait_upside_down" else (874, 402))


async def test_an_upright_or_unreadable_screenshot_is_kept_as_it_is() -> None:
    shot = Shot(JPEG, 402, 874)
    assert await Interface(SCREEN).upright(shot) is shot
    assert await Interface(SCREEN, "landscape_right").upright(shot) is shot
    wide = Screen(2622, 1206, 874, 402, 3.0)
    assert Interface(wide, "landscape_right").screen == Screen(1206, 2622, 402, 874, 3.0)
