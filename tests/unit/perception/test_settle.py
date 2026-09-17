# SPDX-License-Identifier: Apache-2.0
"""Settling by what the screen shows: animations that never stop are let go, real changes are not, and a slow fade is
not taken for stillness. The exact way SimMirror 1.0 settled is pinned in test_wait.py."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.settle import (
    ExactStillness,
    PerceptualStillness,
    ScreenshotSettle,
    Stillness,
    stillness_for,
)
from sim_mirror.perception.snapshot import Snapshot, build
from sim_mirror.testing.fakes import SCREEN, ManualClock
from sim_mirror.testing.pictures import HEIGHT, WIDTH, Box, PictureEngine, picture

TITLE: Box = (10, 20, 100, 12, 20)


class Rig:
    def __init__(self, engine: PictureEngine) -> None:
        self.engine = engine
        self.clock = ManualClock()

    async def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    async def read(self) -> Snapshot:
        return build(tree_from_document(await self.engine.accessibility()), device="iOS", screen=SCREEN, max_elements=9)

    async def settle(self, stillness: Stillness, *, quiet_ms: int = 300, timeout_ms: int = 3000) -> str:
        settle = ScreenshotSettle(read=self.read, source=self.engine, screen=SCREEN, clock=self.clock,
                                  sleep=self.sleep, unreadable=(LookupError,), stillness=stillness)  # fmt: skip
        return await settle.settle(quiet_ms / 1000, timeout_ms / 1000)


def perceptual(tolerance: int = 2) -> PerceptualStillness:
    return PerceptualStillness(columns=32, tolerance=tolerance)


def spinner(look: int, *, extra: tuple[Box, ...] = ()) -> bytes:
    """A title, and a spinner's arm in one of three places in turn."""
    return picture(boxes=[TITLE, (60 + 10 * (look % 3), 170, 10, 10, 0), *extra])


def screens(pictures: Callable[[int], bytes]) -> PictureEngine:
    return PictureEngine(pictures, document={"elements": []})


async def test_a_screen_that_does_not_move_settles_at_once() -> None:
    assert await Rig(screens(lambda _: picture(boxes=[TITLE]))).settle(perceptual()) == "settled after 0ms"


async def test_a_spinner_that_never_stops_is_let_go_and_said() -> None:
    answer = await Rig(screens(spinner)).settle(perceptual())
    # Three arms of four cells each, and the cells their JPEG edges bleed into.
    assert answer == "settled after 450ms (18 small places kept moving and were not watched)"


async def test_the_same_spinner_holds_an_exact_wait_to_its_timeout() -> None:
    assert await Rig(screens(spinner)).settle(ExactStillness()) == "still changing after 3000ms"


async def test_a_single_place_that_keeps_blinking_is_said_as_one() -> None:
    def caret(look: int) -> bytes:
        return picture(boxes=[TITLE, *([(100, 200, 5, 5, 200)] if look % 2 else [])])

    answer = await Rig(screens(caret)).settle(perceptual(tolerance=0))
    assert answer == "settled after 300ms (1 small place kept moving and was not watched)"


async def test_a_label_that_changes_once_starts_the_quiet_again() -> None:
    def saving(look: int) -> bytes:
        return picture(boxes=[TITLE, (10, 200, 40 if look < 5 else 90, 10, 0)])

    assert await Rig(screens(saving)).settle(perceptual(), quiet_ms=1000) == "settled after 750ms"


async def test_a_row_that_appears_while_a_spinner_turns_is_still_seen() -> None:
    def loading(look: int) -> bytes:
        return spinner(look, extra=((0, 250, WIDTH, 30, 90),) if look >= 8 else ())

    answer = await Rig(screens(loading)).settle(perceptual(), quiet_ms=1500)
    assert answer == "settled after 1200ms (18 small places kept moving and were not watched)"


@pytest.mark.parametrize(("tolerance", "answer"), [(3, "settled after 0ms"), (2, "settled after 450ms")])
async def test_changes_within_the_tolerance_count_as_still(tolerance: int, answer: str) -> None:
    # The badge covers two cells, and its JPEG edge a third.
    def badge(look: int) -> bytes:
        return picture(boxes=[TITLE, *([(100, 300, 5, 10, 200)] if look >= 3 else [])])

    assert await Rig(screens(badge)).settle(perceptual(tolerance), quiet_ms=1000) == answer


async def test_a_slow_fade_never_passes_for_still_and_settles_once_it_ends() -> None:
    def fading(look: int) -> bytes:
        return picture(background=255 - 9 * min(look, 20), boxes=[TITLE])

    assert await Rig(screens(fading)).settle(perceptual(), timeout_ms=5000) == "settled after 3000ms"
    assert await Rig(screens(fading)).settle(perceptual(), timeout_ms=1500) == "still changing after 1500ms"


async def test_a_change_across_the_screen_forgets_what_had_kept_moving() -> None:
    def pushed(look: int) -> bytes:
        return spinner(look) if look < 6 else picture(boxes=[(0, 0, WIDTH, HEIGHT // 2, 40)])

    assert await Rig(screens(pushed)).settle(perceptual(), quiet_ms=1000) == "settled after 900ms"


def typed_field() -> dict[str, Any]:
    """A field being typed into, its middle at y 194 points: 77 pixels down a settle screenshot."""
    field = {"type": "TextField", "label": "Title", "value": "x", "traits": ["IsEditing"],
             "frame": {"x": 16, "y": 172, "width": 370, "height": 44}}  # fmt: skip
    return {"elements": [{"type": "Application", "label": "NotesProbe", "children": [field]}]}


async def test_a_field_being_typed_into_is_painted_out_of_one_whole_screenshot() -> None:
    def caret(look: int) -> bytes:
        return picture(boxes=[TITLE, *([(40, 70, 2, 14, 0)] if look % 2 else [])])

    engine = PictureEngine(caret, document=typed_field())
    answer = await Rig(engine).settle(perceptual())
    assert answer == "settled after 0ms (the focused field's row left out)"
    assert {crop for _, _, crop in engine.screenshots} == {None} and engine.looks == len(engine.screenshots)


async def test_a_screenshot_that_cannot_be_decoded_says_the_screen_could_not_be_watched() -> None:
    answer = await Rig(screens(lambda _: b"not a picture")).settle(perceptual())
    assert answer.startswith("could not watch the screen settle: the screenshot could not be decoded")


def test_a_scope_chooses_how_its_waits_settle() -> None:
    config = SimConfig.defaults()
    chosen = stillness_for(config)
    assert isinstance(chosen, PerceptualStillness) and chosen.crops(SCREEN, (10.0, 20.0)) == [None]
    assert isinstance(stillness_for(config.with_values(settle_mode="exact")), ExactStillness)
