# SPDX-License-Identifier: Apache-2.0
"""Why a touch on an element is refused before it lands, said so the agent knows what to do instead."""

from __future__ import annotations

from sim_mirror.core.refusals import touch_refusal
from sim_mirror.perception.snapshot import Element


def test_an_element_nothing_covers_may_be_touched() -> None:
    assert touch_refusal("e1", Element("e1", "button", "Save", None, 201, 400)) is None


def test_one_under_the_keyboard_is_refused_with_how_to_uncover_it() -> None:
    under = Element("e7", "field", "", None, 201.4, 625.6, covered="keyboard")
    assert touch_refusal("e7", under) == (
        "e7 is under the keyboard: a touch at (201,626) would land on the keyboard. First close the keyboard or "
        "scroll it into view and then use e7 again"
    )
