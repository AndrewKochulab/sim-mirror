# SPDX-License-Identifier: Apache-2.0
"""Whether the Mac's current keyboard layout is US-shaped, asked of the preferences daemon each time."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from sim_mirror.platform.keyboard import LAYOUT_QUERY, US_LAYOUTS, mac_keyboard_is_us
from sim_mirror.testing.guards import forbidden


@pytest.mark.parametrize(
    ("code", "out", "us"),
    [
        (0, "com.apple.keylayout.ABC\n", True),
        (0, "com.apple.keylayout.US\n", True),
        (0, "com.apple.keylayout.Ukrainian-PC\n", False),
        (0, "com.apple.keylayout.British\n", False),
        (1, "", False),
    ],
)
async def test_only_a_us_or_abc_layout_counts_and_one_that_cannot_be_read_does_not(
    code: int, out: str, us: bool
) -> None:
    asked: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        asked.append(argv)
        return code, out

    assert await mac_keyboard_is_us(run) is us
    assert asked == [LAYOUT_QUERY] and LAYOUT_QUERY[:2] == ("defaults", "read")


def test_the_layouts_are_the_measured_ones_and_no_test_asks_the_real_mac() -> None:
    assert {"com.apple.keylayout.ABC", "com.apple.keylayout.US"} == US_LAYOUTS
    assert forbidden(LAYOUT_QUERY) == "defaults"
