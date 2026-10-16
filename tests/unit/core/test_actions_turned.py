# SPDX-License-Identifier: Apache-2.0
"""An agent on an app turned to landscape: it sees, touches and looks at the app as it is drawn, and every touch lands
where that is on the device's portrait screen."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from sim_mirror.connectors.base import Crop, HidEvent, Screen, Shot
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.perception.ocr import OcrReaders
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import SCREEN, FakeConnector, FakeEngine, fixture_json
from sim_mirror.testing.pictures import picture
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.device import device_line

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code")


def element(kind: str, label: str, x: float, y: float, width: float = 60, height: float = 60) -> dict[str, Any]:
    frame = {"x": x, "y": y, "width": width, "height": height}
    return {"type": kind, "label": label, "frame": frame, "children": []}


def landscape(orientation: str = "landscape_right") -> dict[str, Any]:
    """A game turned on an iPhone 17 Pro, as the native helper reads it: in the interface's 874x402 points."""
    app = element("Application", "Game", 0, 0, 874, 402)
    app["children"] = [element("Button", "Play", 814, 60), element("Button", "Back", 0, 302)]
    return {"elements": [app], "modal": None, "truncated": False, "orientation": orientation}


class Recording:
    def __init__(self) -> None:
        self.points: list[Sequence[tuple[float, float]]] = []

    def agent(self, kind: str, points: Sequence[tuple[float, float]], duration: float, lead: float) -> None:
        self.points.append(points)


@dataclass
class Rigged:
    rig: DeviceRig
    instance: DeviceInstance
    actions: AgentActions


async def rigged(folder: Path, engine: FakeEngine, pixels: OcrReaders | None = None) -> Rigged:
    folder.mkdir(parents=True, exist_ok=True)
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=engine))
    instance = await rig.up()

    async def sleep(seconds: float) -> None:
        await asyncio.sleep(0)

    return Rigged(rig, instance, AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=sleep, pixels=pixels))


def points(events: list[HidEvent]) -> list[tuple[int, int]]:
    return [(round(event.x), round(event.y)) for event in events if event.kind == "touch"]


async def test_a_snapshot_of_a_turned_app_is_the_app_as_it_is_drawn(tmp_path: Path) -> None:
    r = await rigged(tmp_path, FakeEngine(document=landscape()))
    seen = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)
    # Before, the screen's portrait size cut the button at x 844 off at 402.
    assert seen.splitlines()[0].startswith("iOS 26.5 · Game · 874x402pt landscape · #")
    assert 'e1 button "Play" (844,90)' in seen and 'e2 button "Back" (30,332)' in seen
    assert r.instance.orientation == "landscape_right"
    assert device_line(r.instance).splitlines()[0].endswith("874x402pt @3x landscape")


async def test_a_tap_on_a_turned_app_lands_where_the_element_is_on_the_screen(tmp_path: Path) -> None:
    engine = FakeEngine(document=landscape())
    r = await rigged(tmp_path, engine)
    recording = Recording()
    r.instance.recording = recording  # type: ignore[assignment]
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e1"}, {"tap": [30, 332]}], snapshot="none")
    assert answer.splitlines() == ['ok tap e1 "Play" (844,90)', "ok tap (30,332)"]
    # The screen's portrait points: the interface's top left is the screen's top right.
    assert points(engine.hid_events) == [(312, 844), (312, 844), (70, 30), (70, 30)]
    assert recording.points == [[(312.0, 844.0)], [(70.0, 30.0)]]


async def test_points_and_directions_are_the_turned_apps(tmp_path: Path) -> None:
    engine = FakeEngine(document=landscape("landscape_left"))
    r = await rigged(tmp_path, engine)
    events = r.instance.events.subscribe()
    steps = [{"tap": [870, 10]}, {"swipe": {"from": [437, 201], "direction": "left"}}, {"tap": [10, 870]}]
    lines = (await r.actions.act(r.instance, CALLER, steps, snapshot="none")).splitlines()
    assert lines[0] == "ok tap (870,10)"
    # Left is left along the app: 35% of its 874 points.
    assert lines[1] == "ok swipe (437,201) → (131,201)"
    assert lines[2] == "error step 3: (10,870) is off the 874x402pt screen"
    touched = points(engine.hid_events)
    assert touched[:2] == [(10, 4), (10, 4)]
    assert touched[2] == (201, 437) and touched[-1] == (201, 743)
    published = [events.get_nowait() for _ in range(events.qsize())]
    intents = [event for event in published if event["type"] == "agent" and event["phase"] == "intent"]
    # What screens draw is on the device's screen too, as shares of its portrait size.
    assert intents[0]["gesture"]["points"] == [(round(10 / 402, 4), round(4 / 874, 4))]


def dark_corner(jpeg: bytes) -> bool:
    with Image.open(BytesIO(jpeg)) as image:
        return bool(image.convert("L").getpixel((3, 3)) < 128)  # type: ignore[operator]


class PortraitEngine(FakeEngine):
    """A device whose screenshots are portrait pictures with a dark box in the screen's top right corner."""

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality, crop=crop)
        width = max_width
        height = round(width * (874 / 402 if crop is None else crop.height / crop.width))
        return Shot(picture(width, height, boxes=[(width - 10, 0, 10, 10, 0)]), width, height)


async def test_a_screenshot_of_a_turned_app_is_upright_and_a_region_is_the_apps(tmp_path: Path) -> None:
    engine = PortraitEngine(document=landscape())
    r = await rigged(tmp_path, engine)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)
    [shot] = await r.actions.screenshots(r.instance, CALLER, width=874)
    # Before, it was the portrait screen with the app on its side.
    assert (shot.width, shot.height) == (874, 402) and dark_corner(shot.jpeg)
    assert engine.screenshots[-1] == (402, 70, None)
    [region] = await r.actions.screenshots(r.instance, CALLER, width=400, region={"x": 774, "y": 0, "w": 100, "h": 50})
    # The app's top right corner is the screen's bottom right, cut there and turned upright.
    assert engine.screenshots[-1] == (200, 70, Crop(352, 774, 50, 100))
    assert (region.width, region.height) == (400, 200) and dark_corner(region.jpeg)


IPAD = Screen(width_px=2064, height_px=2752, width_pt=1032, height_pt=1376, scale=2.0)


class RotatedEngine(FakeEngine):
    """A simulator whose device was turned, as measured on iOS 26.5: its screenshots are still the portrait
    framebuffer, with the app's top left corner -- a dark box -- in the corner of the screen the turn put it in."""

    def __init__(self, document: dict[str, Any], screen: Screen, *, right: bool, bottom: bool) -> None:
        super().__init__(document=document, screen=screen)
        self.right = right
        self.bottom = bottom

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality, crop=crop)
        across, down = (self.screen.width_pt, self.screen.height_pt) if crop is None else (crop.width, crop.height)
        width, height = max_width, round(max_width * down / across)
        box = (width - 10 if self.right else 0, height - 10 if self.bottom else 0, 10, 10, 0)
        return Shot(picture(width, height, boxes=[box]), width, height)


@pytest.mark.parametrize(
    ("recorded", "screen", "corner", "header", "alpha", "region"),
    [
        # An iPhone 17 Pro turned left: the interface is landscape right, its top left at the screen's top right.
        (
            "ax-probe-rotated-left-ios26.json",
            SCREEN,
            (True, False),
            "iOS 26.5 · Probe · 874x402pt landscape",
            (281, 219),
            (Crop(322, 0, 80, 120), (874, 402)),
        ),
        # An iPad Pro 13-inch turned upside down: the interface's top left is the screen's bottom right.
        (
            "ax-probe-ipad-upside-down-ios26.json",
            IPAD,
            (True, True),
            "iOS 26.5 · Probe · 1032x1376pt upside down",
            (774, 963),
            (Crop(912, 1296, 120, 80), (1032, 1376)),
        ),
    ],
)
async def test_a_device_turned_in_simulator_is_read_touched_and_looked_at_upright(
    tmp_path: Path,
    recorded: str,
    screen: Screen,
    corner: tuple[bool, bool],
    header: str,
    alpha: tuple[int, int],
    region: tuple[Crop, tuple[int, int]],
) -> None:
    # Recorded from the native helper on simulators turned the way Simulator.app's Device > Rotate turns them. The
    # framebuffer stayed portrait, so the device's turn is handled as an app turning its own interface is.
    engine = RotatedEngine(fixture_json(recorded), screen, right=corner[0], bottom=corner[1])
    r = await rigged(tmp_path, engine)
    seen = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)
    assert seen.splitlines()[0].startswith(header + " · #")
    assert seen.splitlines()[1].startswith('e1 button "Alpha"')
    assert (await r.actions.act(r.instance, CALLER, [{"tap": "e1"}], snapshot="none")).startswith('ok tap e1 "Alpha"')
    # Where the live tap landed on Alpha, in the screen's portrait points.
    assert points(engine.hid_events)[0] == alpha
    [shot] = await r.actions.screenshots(r.instance, CALLER, width=region[1][0])
    assert (shot.width, shot.height) == region[1] and dark_corner(shot.jpeg)
    [cut] = await r.actions.screenshots(r.instance, CALLER, width=240, region={"x": 0, "y": 0, "w": 120, "h": 80})
    assert engine.screenshots[-1][2] == region[0]
    assert (cut.width, cut.height) == (240, 160) and dark_corner(cut.jpeg)


def landscape_grid() -> dict[str, Any]:
    """A grid in an app turned landscape right, scrolled on under its tab bar, as the native helper reads it."""
    app = element("Application", "Grid", 0, 0, 874, 402)
    bar = {**element("Group", "Tab Bar", 0, 352, 874, 50), "traits": ["TabBar"]}
    bar["children"] = [element("Button", "Library", 400, 356, 74, 42)]
    app["children"] = [
        element("Button", "Row 5", 100, 330, 200, 40),
        element("Button", "Row 9", 700, 360, 150, 40),
        bar,
    ]
    return {"elements": [app], "modal": None, "truncated": False, "orientation": "landscape_right"}


async def test_what_a_tab_bar_along_a_turned_apps_bottom_covers_is_said_and_refused(tmp_path: Path) -> None:
    engine = FakeEngine(document=landscape_grid())
    r = await rigged(tmp_path, engine)
    seen = (await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)).splitlines()
    # Covers are found in the app's 874x402 points: on the portrait 402x874 screen, the row at x 700 and the bar past
    # x 402 were cut off.
    assert seen[1:] == [
        'e1 button "Row 5" (200,341)',
        'e2 button "Row 9" (775,380) covered by tab bar',
        'e3 button "Library" (437,377)',
    ]
    said = (await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")).splitlines()
    assert said[0].startswith('error step 1: e2 "Row 9" is under the tab bar: a touch at (775,380)')
    assert engine.hid_events == []
    # The part of a row the bar leaves clear is where a tap lands, turned onto the screen like any other point.
    assert await r.actions.act(r.instance, CALLER, [{"tap": "e1"}], snapshot="none") == 'ok tap e1 "Row 5" (200,341)'
    assert points(engine.hid_events) == [(61, 200), (61, 200)]
