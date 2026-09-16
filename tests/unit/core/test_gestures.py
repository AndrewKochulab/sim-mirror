# SPDX-License-Identifier: Apache-2.0
"""Gestures touch by touch: where each event lands and when, and a player that keeps the time."""

from __future__ import annotations

import pytest

from sim_mirror.connectors.base import BUTTONS, HidEvent
from sim_mirror.core import gestures
from sim_mirror.protocol import KEY_NAMES


def test_the_keys_are_the_protocols_own() -> None:
    assert tuple(gestures.KEYS) == KEY_NAMES


def test_a_tap_is_a_finger_down_then_up_on_the_same_point() -> None:
    assert gestures.tap(10, 20) == [(0.0, HidEvent.touch("down", 10, 20)), (0.05, HidEvent.touch("up", 10, 20))]
    assert gestures.long_press(1, 2)[-1] == (0.8, HidEvent.touch("up", 1, 2))


def test_a_swipe_moves_evenly_one_frame_at_a_time() -> None:
    events = gestures.swipe((0, 0), (0, 100), 0.1)
    assert [event.phase for _at, event in events] == ["down"] * 6 + ["up"]
    assert [event.y for _at, event in events] == pytest.approx([0, 100 / 6, 200 / 6, 50, 400 / 6, 500 / 6, 100])
    assert [at for at, _event in events] == pytest.approx([0, 1 / 60, 2 / 60, 3 / 60, 4 / 60, 5 / 60, 0.1])
    assert gestures.duration(events) == 0.1 and gestures.duration([]) == 0.0


def test_a_drag_follows_its_path_by_distance_not_by_corner() -> None:
    events = gestures.drag([(0, 0), (30, 0), (30, 30)], 3 / 60)
    assert [(event.x, event.y) for _at, event in events] == [(0, 0), (20, 0), (30, 10), (30, 30)]


def test_a_drag_that_goes_nowhere_holds_and_a_zero_duration_is_one_frame() -> None:
    assert gestures.drag([(5, 5)], 0) == [
        (0.0, HidEvent.touch("down", 5, 5)),
        (gestures.STEP_S, HidEvent.touch("up", 5, 5)),
    ]
    still = gestures.drag([(5, 5), (5, 5)], 0.05)
    assert {(event.x, event.y) for _at, event in still} == {(5, 5)} and len(still) == 4
    with pytest.raises(ValueError, match="at least one point"):
        gestures.drag([], 1)


def test_keys_buttons_select_all_and_paste() -> None:
    assert gestures.key("return") == [(0.0, HidEvent.key(40, "down")), (0.05, HidEvent.key(40, "up"))]
    with pytest.raises(ValueError, match="not a key"):
        gestures.key("f13")
    assert [event.button for _at, event in gestures.button("home")] == ["home", "home"]
    with pytest.raises(ValueError, match="not a button"):
        gestures.button("power")
    assert set(BUTTONS) >= {"home", "lock", "side", "siri"}
    pressed = gestures.paste()
    assert [(event.code, event.phase) for _at, event in pressed] == [
        (gestures.COMMAND_KEY, "down"),
        (gestures.V_KEY, "down"),
        (gestures.V_KEY, "up"),
        (gestures.COMMAND_KEY, "up"),
    ]
    selected = gestures.select_all()
    assert [(at, event.code, event.phase) for at, event in selected] == [
        (0.0, gestures.COMMAND_KEY, "down"),
        (0.0, gestures.A_KEY, "down"),
        (gestures.TAP_S, gestures.A_KEY, "up"),
        (gestures.TAP_S, gestures.COMMAND_KEY, "up"),
    ]


async def test_play_yields_each_event_when_it_is_due() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(round(seconds, 3))
        now[0] += seconds

    a, b, c, d = (HidEvent.touch("down", i, i) for i in range(4))
    events = [(0.0, a), (0.05, b), (0.05, c), (0.2, d)]
    played = [event async for event in gestures.play(events, sleep=sleep, clock=lambda: now[0])]
    assert played == [a, b, c, d] and slept == [0.05, 0.15]


def test_text_is_typed_as_the_keys_a_us_keyboard_has_holding_shift_where_it_needs_it() -> None:
    events = gestures.typed("aZ\n", hold_s=0.02)
    assert events == [
        (0.0, HidEvent.key(4, "down")),
        (0.02, HidEvent.key(4, "up")),
        (0.02, HidEvent.key(gestures.SHIFT_KEY, "down")),
        (0.02, HidEvent.key(29, "down")),
        (0.04, HidEvent.key(29, "up")),
        (0.04, HidEvent.key(gestures.SHIFT_KEY, "up")),
        (0.04, HidEvent.key(40, "down")),
        (0.06, HidEvent.key(40, "up")),
    ]
    printable = "".join(chr(code) for code in range(32, 127))
    typed = gestures.typed(printable)
    assert typed is not None and gestures.duration(typed) == pytest.approx(len(printable) * gestures.KEY_HOLD_S)
    # Every printable ASCII character has a key, and each is one of the 96 a US keyboard types.
    assert set(printable) <= set(gestures.CHARACTER_KEYS) and len(gestures.CHARACTER_KEYS) == 96
    assert gestures.typed("") == []


@pytest.mark.parametrize("text", ["café", "wifi 😀", "tab\there", "Київ"])
def test_text_with_a_character_no_key_types_is_not_typed_in_part(text: str) -> None:
    assert gestures.typed(text) is None
