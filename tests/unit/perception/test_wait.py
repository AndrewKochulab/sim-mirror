# SPDX-License-Identifier: Apache-2.0
"""Waiting for text to come or go, and for the screen to settle -- with a focused field's caret left out."""

from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError, Crop, Shot
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.settle import ScreenshotSettle
from sim_mirror.perception.snapshot import Snapshot, build
from sim_mirror.perception.wait import Wait, Waiter, parse_wait
from sim_mirror.testing.fakes import SCREEN, FakeEngine, ManualClock, fixture_json
from sim_mirror.validation import Invalid

SETTINGS = fixture_json("ax-settings-interactable.json")


class Unreadable(Exception):
    pass


class Rig:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine
        self.clock = ManualClock()
        self.reads = 0
        self.fail_reads_after: int | None = None

    async def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    async def read(self) -> Snapshot:
        self.reads += 1
        if self.fail_reads_after is not None and self.reads > self.fail_reads_after:
            raise Unreadable("reading the screen failed")
        document = await self.engine.accessibility()
        return build(tree_from_document(document), device="iOS 26.5", screen=SCREEN, max_elements=120)

    def waiter(self) -> Waiter:
        settle = ScreenshotSettle(read=self.read, source=self.engine, screen=SCREEN, clock=self.clock,
                                  sleep=self.sleep, unreadable=(Unreadable,))  # fmt: skip
        return Waiter(read=self.read, settle=settle, clock=self.clock, sleep=self.sleep)

    async def run(self, spec: Any) -> str:
        return await self.waiter().run(parse_wait(spec))


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"for": "a", "gone": "b"}, 'give one of {"for": text}, {"gone": text} or {"settle_ms": ms}'),
        ("Done", "give one of"),
        ({"for": ""}, "wait takes text to look for"),
        ({"gone": 3}, "wait takes text to look for"),
        ({"for": "a", "timeout_ms": 5}, "timeout_ms must be a whole number of milliseconds from 100 to 10000"),
        ({"settle_ms": 1}, "settle_ms must be a whole number of milliseconds from 100 to 3000"),
    ],
)
def test_a_wait_asked_for_wrongly_says_what_would_do(spec: Any, message: str) -> None:
    with pytest.raises(Invalid) as refused:
        parse_wait(spec)
    assert str(refused.value).startswith(message)


def test_a_wait_asked_for_rightly_is_read_with_its_defaults() -> None:
    assert parse_wait({"for": "Done"}) == Wait("for", 5.0, text="Done")
    assert parse_wait({"gone": "Loading", "timeout_ms": 300}) == Wait("gone", 0.3, text="Loading")
    assert parse_wait({"settle_ms": 400}) == Wait("settle", 5.0, quiet_s=0.4)


class Arriving(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        done = {"type": "Button", "label": "Done", "frame": {"x": 300, "y": 60, "width": 80, "height": 40}}
        self.later = {
            "elements": [{**SETTINGS["elements"][0], "children": [*SETTINGS["elements"][0]["children"], done]}]
        }

    async def accessibility(self) -> dict[str, Any]:
        self.reads += 1
        return self.later if self.reads >= 3 else self.document


async def test_waiting_for_text_that_comes_polls_until_it_is_there() -> None:
    rig = Rig(Arriving())
    assert await rig.run({"for": "Done", "timeout_ms": 1000}) == 'waited 300ms for "Done"'
    assert await rig.run({"for": "done"}) == 'waited 0ms for "done"'


async def test_waiting_for_text_to_go_or_that_never_comes_gives_up_saying_so() -> None:
    rig = Rig(FakeEngine())
    assert await rig.run({"gone": "General", "timeout_ms": 300}) == 'still showing "General" after 300ms'
    assert await rig.run({"for": "Bluetooth", "timeout_ms": 150}) == 'still no "Bluetooth" after 150ms'
    rig.engine.document = {"elements": []}
    assert await rig.run({"gone": "General"}) == 'waited 0ms for "General" to go'


class Animating(FakeEngine):
    """A screen that changes for its first `moving` screenshots, then keeps still."""

    def __init__(self, moving: int) -> None:
        super().__init__()
        self.moving = moving
        self.taken = 0

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.taken += 1
        frame = self.taken if self.taken <= self.moving else 0
        return Shot(b"frame-%d" % frame, max_width, 100)


@pytest.mark.parametrize(
    ("moving", "spec", "line"),
    [
        (0, {"settle_ms": 300}, "settled after 0ms"),
        (2, {"settle_ms": 300}, "settled after 300ms"),
        (99, {"settle_ms": 300, "timeout_ms": 450}, "still changing after 450ms"),
    ],
)
async def test_waiting_for_an_animation_to_settle(moving: int, spec: dict[str, int], line: str) -> None:
    assert await Rig(Animating(moving)).run(spec) == line


class Cycling(FakeEngine):
    """A screen showing `pictures` pictures in turn: 2 is a blinking caret, 3 a spinner."""

    def __init__(self, pictures: int) -> None:
        super().__init__()
        self.pictures = pictures
        self.taken = 0

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.taken += 1
        return Shot(b"picture-%d" % (self.taken % self.pictures), max_width, 100)


async def test_a_spinner_keeps_a_settle_wait_waiting() -> None:
    assert await Rig(Cycling(3)).run({"settle_ms": 300, "timeout_ms": 3000}) == "still changing after 3000ms"


def typed_field(*, editing: bool = True) -> dict[str, Any]:
    """NotesProbe with its Title field -- middle at y 194 -- being typed into, or not."""
    field = {"type": "TextField", "label": "Title", "value": "x", "frame": {"x": 16, "y": 172, "width": 370,
             "height": 44}, "traits": ["IsEditing"] if editing else []}  # fmt: skip
    save = {"type": "Button", "label": "Save", "frame": {"x": 16, "y": 300, "width": 370, "height": 44}}
    return {"elements": [{"type": "Application", "label": "NotesProbe", "children": [field, save]}]}


class CaretFading(FakeEngine):
    """A focused field whose caret fades through several pictures, with nothing else moving. A screenshot that takes
    in the field's row (164-224pt) is new every time; one that does not is the same."""

    def __init__(self, *, editing: bool = True) -> None:
        super().__init__(document=typed_field(editing=editing))
        self.taken = 0
        self.crops: list[Crop | None] = []

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.taken += 1
        self.crops.append(crop)
        if crop is None or (crop.y < 224 and crop.y + crop.height > 164):
            return Shot(b"caret-%d" % (self.taken % 5), max_width, 100)
        return Shot(b"still-%d" % round(crop.y), max_width, 100)


async def test_a_caret_fading_in_a_field_being_typed_into_does_not_hold_a_settle_wait() -> None:
    engine = CaretFading()
    answer = await Rig(engine).run({"settle_ms": 300, "timeout_ms": 3000})
    assert answer == "settled after 0ms (the focused field's row left out)"
    top, bottom = engine.crops[0], engine.crops[1]
    assert top == Crop(0.0, 0.0, 402.0, 164.0) and bottom == Crop(0.0, 224.0, 402.0, 650.0)


async def test_without_a_field_known_to_be_editing_the_whole_screen_is_watched() -> None:
    idle = CaretFading(editing=False)
    assert await Rig(idle).run({"settle_ms": 300, "timeout_ms": 1000}) == "still changing after 1050ms"
    assert set(idle.crops) == {None}
    unreadable = CaretFading()
    rig = Rig(unreadable)
    rig.fail_reads_after = 0
    assert await rig.run({"settle_ms": 300, "timeout_ms": 1000}) == "still changing after 1050ms"
    assert set(unreadable.crops) == {None}


@pytest.mark.parametrize(("frame_y", "watched"), [(-12, {(46.0, 828.0)}), (850, {(0.0, 832.0)})])
async def test_a_field_at_an_edge_of_the_screen_leaves_only_the_other_side_watched(
    frame_y: int, watched: set[tuple[float, float]]
) -> None:
    engine = CaretFading()
    engine.document["elements"][0]["children"][0]["frame"]["y"] = frame_y
    await Rig(engine).run({"settle_ms": 300, "timeout_ms": 1000})
    assert {(crop.y, crop.height) for crop in engine.crops if crop is not None} == watched


class HeldThenChanged(FakeEngine):
    """A picture held for its first `held` screenshots -- something loading -- then a new one that stays."""

    def __init__(self, held: int) -> None:
        super().__init__()
        self.held = held
        self.taken = 0

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.taken += 1
        return Shot(b"loading" if self.taken <= self.held else b"loaded", max_width, 100)


async def test_one_change_after_a_picture_held_a_while_is_not_taken_for_blinking() -> None:
    assert await Rig(HeldThenChanged(10)).run({"settle_ms": 2000, "timeout_ms": 5000}) == "settled after 1500ms"


async def test_a_screen_that_cannot_be_watched_settling_says_so() -> None:
    engine = FakeEngine()
    engine.screenshot_errors = [ConnectorError("taking a screenshot failed: gone")]
    answer = await Rig(engine).run({"settle_ms": 200})
    assert answer == "could not watch the screen settle: taking a screenshot failed: gone"
