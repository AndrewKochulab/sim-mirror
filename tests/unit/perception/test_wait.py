# SPDX-License-Identifier: Apache-2.0
"""Waiting for text to come, go or hold -- matched as a part, the whole or a pattern -- and for the screen to settle,
with a focused field's caret left out."""

from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError, Crop, Shot
from sim_mirror.perception.orientation import Orientation
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.settle import ScreenshotSettle
from sim_mirror.perception.snapshot import REGEX_TEXT_MAX, Snapshot, build
from sim_mirror.perception.wait import Wait, Waited, Waiter, parse_wait
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
        self.orientation: Orientation = "portrait"
        #: Whether the wait is told which way the app was turned when last read; without, it takes it for upright.
        self.told_orientation = False

    async def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    async def read(self) -> Snapshot:
        self.reads += 1
        if self.fail_reads_after is not None and self.reads > self.fail_reads_after:
            raise Unreadable("reading the screen failed")
        tree = tree_from_document(await self.engine.accessibility())
        self.orientation = tree.orientation
        return build(tree, device="iOS 26.5", screen=SCREEN, max_elements=120)

    def waiter(self) -> Waiter:
        told = (lambda: self.orientation) if self.told_orientation else None
        settle = ScreenshotSettle(read=self.read, source=self.engine, screen=SCREEN, clock=self.clock,
                                  sleep=self.sleep, unreadable=(Unreadable,), orientation=told)  # fmt: skip
        return Waiter(read=self.read, settle=settle, clock=self.clock, sleep=self.sleep, unreadable=(Unreadable,))

    async def waited(self, spec: Any) -> Waited:
        return await self.waiter().run(parse_wait(spec))

    async def run(self, spec: Any) -> str:
        return (await self.waited(spec)).line


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"for": "a", "gone": "b"}, 'give one of {"for": text}, {"gone": text}, {"stable": {"for": text, "ms": ms}}'),
        ("Done", "give one of"),
        ({"for": ""}, "wait takes text to look for"),
        ({"gone": 3}, "wait takes text to look for"),
        ({"for": "a" * 501}, "for is at most 500 characters"),
        ({"for": "a", "timeout_ms": 5}, "timeout_ms must be a whole number of milliseconds from 100 to 120000"),
        ({"for": "a", "timeout_ms": 120001}, "timeout_ms must be a whole number of milliseconds from 100 to 120000"),
        ({"settle_ms": 1}, "settle_ms must be a whole number of milliseconds from 100 to 3000"),
        # A key a wait does not take was dropped, and the wait ran as if it had not been given.
        ({"for": "a", "timeout": 3000}, 'wait does not take "timeout"; it takes "for", "gone", "stable", "settle_ms"'),
        (
            {"for": {"exact": "a", "whole": True}},
            '"for" does not take "whole"; it takes "text", "exact", "regex", "case"',
        ),
        ({"for": {"exact": "a", "regex": "a"}}, '"for" is text, found in a label or value ignoring case, or {"exact"'),
        ({"gone": {}}, '"gone" is text, found in a label or value ignoring case'),
        ({"for": {"exact": ""}}, '"exact" is text of 1 to 500 characters'),
        ({"for": {"regex": "a" * 201}}, '"regex" is text of 1 to 200 characters'),
        ({"for": {"text": "a", "case": "yes"}}, '"case" is true or false'),
        ({"for": {"regex": "Lock(ed"}}, "the regex 'Lock(ed' does not compile: missing ), unterminated subpattern"),
        # Searched on the loop every agent is answered on, it would hold them all for minutes (test_backtracking).
        (
            {"for": {"regex": "(a+)+$"}},
            "the regex '(a+)+$' is refused: nested repeats can take minutes to match; repeat the inner part only",
        ),
        (
            {"stable": {"gone": {"regex": "(a|aa)+$", "case": False}, "ms": 300}},
            "the regex '(a|aa)+$' is refused: a repeat of alternatives that can begin with the same character",
        ),
        ({"stable": "Locked"}, 'stable is {"for": text, "ms": ms}, or {"gone": text, "ms": ms}'),
        ({"stable": {"for": "Locked"}}, "stable is"),
        ({"stable": {"for": "a", "gone": "b", "ms": 300}}, "stable is"),
        (
            {"stable": {"for": "a", "ms": 300, "for_ms": 1}},
            'stable does not take "for_ms"; it takes "for", "gone", "ms"',
        ),
        ({"stable": {"for": "a", "ms": 50}}, "stable ms must be a whole number of milliseconds from 100 to 120000"),
        ({"stable": {"for": "a", "ms": 3000}, "timeout_ms": 2000}, "stable ms (3000) must be within timeout_ms (2000)"),
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
    assert parse_wait({"for": "Done", "timeout_ms": 120000}).timeout_s == 120.0
    # How long a stable wait may run is how long it must hold, and the usual timeout beyond, at most two minutes.
    assert parse_wait({"stable": {"for": "Locked", "ms": 30000}}) == Wait(
        "stable", 35.0, text="Locked", present=True, hold_s=30.0
    )
    assert parse_wait({"stable": {"gone": "Moving", "ms": 118000}}).timeout_s == 120.0
    exact = parse_wait({"for": {"exact": "Locked"}}).match
    assert exact is not None and (exact.how, exact.case) == ("exact", True)
    loose = parse_wait({"for": {"regex": "^lock", "case": False}}).match
    assert loose is not None and loose.describe() == "/^lock/i"


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
    # Seen by the read at 300ms -- not "after" it: a read takes time, and what it saw was there while it ran.
    assert (
        await rig.run({"for": "Done", "timeout_ms": 1000})
        == 'waited for "Done": seen in a read 300ms after the last step'
    )
    assert await rig.run({"for": "done"}) == 'waited for "done": seen in a read 0ms after the last step'


async def test_when_a_wait_is_met_is_said_as_the_read_that_saw_it_from_its_start_to_its_end() -> None:
    rig = Rig(Arriving())
    slow = rig.read

    async def taking_a_while() -> Snapshot:
        rig.clock.now += 0.4
        snapshot = await slow()
        rig.clock.now += 0.4
        return snapshot

    rig.read = taking_a_while  # type: ignore[method-assign]
    # Reads 0-800, 950-1750 and 1900-2700ms: the third saw Done. Before, it said 2700ms, a whole read late.
    assert await rig.run({"for": "Done"}) == 'waited for "Done": seen in a read 1900-2700ms after the last step'


async def test_waiting_for_text_to_go_or_that_never_comes_gives_up_saying_so() -> None:
    rig = Rig(FakeEngine())
    assert await rig.run({"gone": "General", "timeout_ms": 300}) == 'still showing "General" after 300ms'
    assert await rig.run({"for": "Bluetooth", "timeout_ms": 150}) == 'still no "Bluetooth" after 150ms'
    rig.engine.document = {"elements": []}
    assert await rig.run({"gone": "General"}) == 'waited for "General" to go: gone in a read 0ms after the last step'
    assert (await rig.waited({"for": "General", "timeout_ms": 100})).met is False


def labelled(*labels: str, value: str = "") -> FakeEngine:
    """A screen of one text per label -- the first given `value` too -- one under the other."""
    children = [
        {"type": "StaticText", "label": label, "frame": {"x": 20, "y": 100 + 50 * index, "width": 360, "height": 30}}
        for index, label in enumerate(labels)
    ]
    if value and children:
        children[0]["value"] = value
    return FakeEngine(document={"elements": [{"type": "Application", "label": "Lock", "children": children}]})


async def test_a_wait_for_exactly_locked_is_not_met_by_unlocked_and_one_for_it_to_go_ends_while_unlocked_shows() -> (
    None
):
    rig = Rig(labelled("Unlocked"))
    # A string is a part of a label, ignoring case: Locked was taken to be on screen while Unlocked was.
    assert await rig.run({"for": "Locked"}) == 'waited for "Locked": seen in a read 0ms after the last step'
    exact = await rig.waited({"for": {"exact": "Locked"}, "timeout_ms": 300})
    assert exact == Waited('still no exactly "Locked" after 300ms', False)
    gone = await rig.run({"gone": {"exact": "Locked"}})
    assert gone == 'waited for exactly "Locked" to go: gone in a read 0ms after the last step'
    rig.engine.document = labelled("Locked").document
    assert (await rig.waited({"for": {"exact": "Locked"}})).met
    assert not (await rig.waited({"for": {"exact": "locked"}, "timeout_ms": 100})).met
    assert (await rig.waited({"for": {"exact": "locked", "case": False}})).met
    assert (await rig.waited({"for": {"regex": "^Lock(ed|ing)$"}})).met
    assert (await rig.waited({"gone": {"regex": "^Unlock"}})).met
    assert not (await rig.waited({"for": {"text": "locked", "case": True}, "timeout_ms": 100})).met
    assert (await rig.run({"for": {"text": "Lock", "case": True}})).startswith('waited for "Lock", with its case:')


async def test_a_wait_matches_the_whole_of_a_long_label_and_a_value_not_the_part_a_line_shows() -> None:
    long = "Your door was locked from the app at 10:42 by someone in the household, and the alarm was set to Away"
    rig = Rig(labelled(long, value="Away  mode\non"))
    line = next(line for line in (await rig.read()).text().splitlines() if "Your door" in line)
    assert "set to Away" not in line, "the line cuts the label"
    assert (await rig.waited({"for": "alarm was set to Away"})).met
    assert (await rig.waited({"for": {"exact": long}})).met
    assert (await rig.waited({"for": {"exact": "Away mode on"}})).met, "runs of white space are one space"


async def test_a_regex_is_searched_in_the_start_of_a_label_however_long_the_label_is() -> None:
    """A text view's whole content can be a label: a regex is searched in its first characters only, so no label is
    long enough to keep a search running."""
    long = "Locked " + "x" * REGEX_TEXT_MAX + " by the app"
    rig = Rig(labelled(long))
    assert (await rig.waited({"for": {"regex": "^Locked x+"}})).met
    assert not (await rig.waited({"for": {"regex": "by the app$"}, "timeout_ms": 100})).met
    assert (await rig.waited({"for": "by the app"})).met, "text that is not a regex is found in the whole label"


async def test_a_stable_wait_ends_once_the_text_has_held_and_not_while_it_comes_and_goes() -> None:
    class Flickering(FakeEngine):
        """Locked for its first reads, then Unlocked for one, then Locked from then on."""

        def __init__(self, gap: int) -> None:
            super().__init__(document=labelled("Locked").document)
            self.reads, self.gap = 0, gap

        async def accessibility(self) -> dict[str, Any]:
            self.reads += 1
            return labelled("Unlocked" if self.reads == self.gap else "Locked").document

    rig = Rig(Flickering(gap=2))
    held = await rig.run({"stable": {"for": {"exact": "Locked"}, "ms": 300}})
    # Read at 0 (Locked), 150 (Unlocked), 300, 450 and 600 (Locked): held from the read at 300 to the one at 600.
    assert (
        held == 'waited for exactly "Locked" to hold 300ms: held from a read 300ms after the last step to one at 600ms'
    )
    never = Rig(labelled("Unlocked"))
    assert await never.waited({"stable": {"for": {"exact": "Locked"}, "ms": 300}, "timeout_ms": 600}) == Waited(
        'exactly "Locked" did not hold 300ms within 600ms (held at most 0ms)', False
    )
    gone = Rig(labelled("Unlocked"))
    said = await gone.run({"stable": {"gone": "Moving", "ms": 150}})
    assert said == 'waited for "Moving" gone to hold 150ms: held from a read 0ms after the last step to one at 150ms'


async def test_a_read_that_fails_during_a_wait_is_tried_again_until_the_wait_gives_up() -> None:
    rig = Rig(labelled("Locked"))
    failing = [True, True]

    async def flaky() -> Snapshot:
        if failing:
            failing.pop()
            raise Unreadable("reading the screen failed: the tree is being rebuilt")
        return await Rig.read(rig)

    rig.read = flaky  # type: ignore[method-assign]
    # Before, the first failed read ended the wait at once.
    assert await rig.run({"for": "Locked"}) == 'waited for "Locked": seen in a read 300ms after the last step'
    rig.fail_reads_after = 0
    rig.read = Rig.read.__get__(rig)  # type: ignore[method-assign]
    stopped = await rig.waited({"for": "Locked", "timeout_ms": 300})
    assert stopped == Waited("wait stopped: the screen could not be read for 300ms: reading the screen failed", False)
    held = await rig.waited({"stable": {"for": "Locked", "ms": 150}, "timeout_ms": 300})
    assert held.line.startswith("wait stopped: the screen could not be read") and not held.met


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


def turned_field(orientation: Orientation) -> dict[str, Any]:
    """NotesProbe turned on an iPhone 17 Pro, its Title field -- middle at y 194 of the app's points -- being typed
    into: the app 874x402 points on its side, 402x874 upside down."""
    across, down = (402, 874) if orientation == "portrait_upside_down" else (874, 402)
    document = typed_field()
    document["elements"][0]["children"][0]["frame"]["width"] = across - 32
    document["elements"][0]["frame"] = {"x": 0, "y": 0, "width": across, "height": down}
    return {**document, "orientation": orientation}


class TurnedCaretFading(FakeEngine):
    """A turned app's focused field whose caret fades, with nothing else moving: a screenshot of the device's screen
    that takes in `row` -- the field's row, where it lies on the portrait screen -- is new every time."""

    def __init__(self, orientation: Orientation, row: Crop) -> None:
        super().__init__(document=turned_field(orientation))
        self.row = row
        self.taken = 0
        self.crops: list[Crop | None] = []

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.taken += 1
        self.crops.append(crop)
        row = self.row
        if crop is None or (
            crop.x < row.x + row.width
            and row.x < crop.x + crop.width
            and crop.y < row.y + row.height
            and row.y < crop.y + crop.height
        ):
            return Shot(b"caret-%d" % (self.taken % 5), max_width, 100)
        return Shot(b"still-%d-%d" % (round(crop.x), round(crop.y)), max_width, 100)


@pytest.mark.parametrize(
    ("orientation", "row", "watched"),
    [
        # The app's top along the screen's right edge: its row 164-224 is a strip down the screen, x 178-238.
        ("landscape_right", Crop(178, 0, 60, 874), [Crop(0, 0, 178, 874), Crop(238, 0, 164, 874)]),
        # Along the left edge: x 164-224.
        ("landscape_left", Crop(164, 0, 60, 874), [Crop(0, 0, 164, 874), Crop(224, 0, 178, 874)]),
        # Upside down: the row is across the screen again, 650-710 from its top.
        ("portrait_upside_down", Crop(0, 650, 402, 60), [Crop(0, 0, 402, 650), Crop(0, 710, 402, 164)]),
    ],
)
async def test_a_caret_fading_in_a_turned_apps_field_is_left_out_where_the_row_is_on_the_screen(
    orientation: Orientation, row: Crop, watched: list[Crop]
) -> None:
    engine = TurnedCaretFading(orientation, row)
    rig = Rig(engine)
    rig.told_orientation = True
    answer = await rig.run({"settle_ms": 300, "timeout_ms": 3000})
    # Before, the row was left out across the portrait screen, where the caret is not, and the wait timed out.
    assert answer == "settled after 0ms (the focused field's row left out)"
    assert engine.crops[:2] == watched


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
