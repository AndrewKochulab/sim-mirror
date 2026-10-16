# SPDX-License-Identifier: Apache-2.0
"""Perception of an app turned on the device's portrait screen: the text in its pixels is read from its screenshot
turned upright, merged into its snapshot in the app's points, tapped where it is on the screen and outlined there for
a viewer."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from sim_mirror.connectors.base import Crop, Screen, Shot
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.perception.ocr import OcrReaders, RecognitionOptions, RecognizedLine
from sim_mirror.perception.orientation import Interface, Orientation
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import SCREEN, FakeConnector, FakeEngine, fixture_json
from sim_mirror.testing.pictures import picture
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.testing.vision import text_line

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code")
IPAD = Screen(width_px=2064, height_px=2752, width_pt=1032, height_pt=1376, scale=2.0)
#: How many pixels across a screenshot read for text is, here: small, since only its corners are looked at.
ACROSS = 80


class TurnedScreen(FakeEngine):
    """A simulator showing an app turned on it, as measured on iOS 26.5: its screenshots are the portrait framebuffer,
    with the app's top left corner -- a dark box -- wherever the turn put it on the screen."""

    def __init__(self, document: dict[str, Any], screen: Screen) -> None:
        super().__init__(document=document, screen=screen)
        interface = Interface(screen, document["orientation"])
        x, y = interface.on_device(0, 0)
        self.right, self.bottom = x > 0, y > 0

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality, crop=crop)
        width, height = ACROSS, round(ACROSS * self.screen.height_pt / self.screen.width_pt)
        box = (width - 8 if self.right else 0, height - 8 if self.bottom else 0, 8, 8, 0)
        return Shot(picture(width, height, boxes=[box]), width, height)


class UprightRecognizer:
    """Reads `lines` only in a picture that is upright -- the app's top left corner at its own -- and nothing in one on
    its side or upside down: a reader that finds the text only the way up the app draws it."""

    def __init__(self, *lines: RecognizedLine) -> None:
        self.lines = lines

    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        await asyncio.sleep(0)
        with Image.open(BytesIO(jpeg)) as image:
            upright = image.convert("L").getpixel((2, 2)) < 128  # type: ignore[operator]
        return self.lines if upright else ()

    async def close(self) -> None:
        return None


async def acting(
    folder: Path, engine: FakeEngine, pixels: OcrReaders | None = None
) -> tuple[DeviceRig, DeviceInstance, AgentActions]:
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=engine))
    instance = await rig.up()

    async def sleep(seconds: float) -> None:
        rig.clock.now += seconds
        await asyncio.sleep(0)

    return rig, instance, AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=sleep, pixels=pixels)


def turned(recorded: str, orientation: Orientation) -> dict[str, Any]:
    """A tree recorded from the native helper, turned as given: an app's frames are in its own points, the same
    whichever way it lies, so only the way it says changes."""
    return {**fixture_json(recorded), "orientation": orientation}


@pytest.mark.parametrize(
    ("document", "screen", "text", "seen", "touched", "outlined"),
    [
        # An iPhone 17 Pro turned left: the app is landscape right, its top along the screen's right edge.
        (
            turned("ax-probe-rotated-left-ios26.json", "landscape_right"),
            SCREEN,
            (40, 340, 200, 30),
            ("874x402pt landscape", (140, 355)),
            (47, 140),
            Crop(32, 40, 30, 200),
        ),
        # Turned the other way: landscape left, its top along the left edge.
        (
            turned("ax-probe-rotated-left-ios26.json", "landscape_left"),
            SCREEN,
            (40, 340, 200, 30),
            ("874x402pt landscape", (140, 355)),
            (355, 734),
            Crop(340, 634, 30, 200),
        ),
        # An iPad Pro 13-inch upside down: the app's top left at the screen's bottom right.
        (
            fixture_json("ax-probe-ipad-upside-down-ios26.json"),
            IPAD,
            (40, 1200, 200, 30),
            ("1032x1376pt upside down", (140, 1215)),
            (892, 161),
            Crop(792, 146, 200, 30),
        ),
    ],
)
async def test_text_in_a_turned_apps_pixels_is_read_upright_and_merged_where_the_app_shows_it(
    tmp_path: Path,
    document: dict[str, Any],
    screen: Screen,
    text: tuple[float, float, float, float],
    seen: tuple[str, tuple[int, int]],
    touched: tuple[int, int],
    outlined: Crop,
) -> None:
    interface = Interface(screen, document["orientation"])
    recognizer = UprightRecognizer(text_line("High score", *text, screen=interface.screen))
    engine = TurnedScreen(document, screen)
    rig, instance, actions = await acting(tmp_path, engine, OcrReaders(recognizer, supported=True))
    rig.config.set(ocr_mode="merge", ocr_overlay=True)
    events = instance.events.subscribe()
    lines = (await actions.snapshot(instance, CALLER, mode="full", max_elements=50)).splitlines()
    # Before, the text was left out of a turned app's snapshot with a note, and its portrait picture never read.
    size, (x, y) = seen
    assert f" · {size} · #" in lines[0]
    [line] = [line for line in lines if '"High score"' in line]
    assert line == f'e6 text "High score" ({x},{y})' and lines[-1].startswith("1 line was read from the screen's")
    # A tap on the line lands where it is on the device's screen.
    assert await actions.act(instance, CALLER, [{"tap": "e6"}], snapshot="none") == f'ok tap e6 "High score" ({x},{y})'
    assert (round(engine.hid_events[0].x), round(engine.hid_events[0].y)) == touched
    # A viewer shows the device's own screen: its outline is there.
    published = [events.get_nowait() for _ in range(events.qsize())]
    [box] = next(event["boxes"] for event in published if event["type"] == "screen_text" and event["boxes"])
    width, height = float(screen.width_pt), float(screen.height_pt)
    expected = (outlined.x / width, outlined.y / height, outlined.width / width, outlined.height / height)
    assert (box["x"], box["y"], box["w"], box["h"]) == tuple(round(share, 4) for share in expected)

