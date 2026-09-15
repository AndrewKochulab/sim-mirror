# SPDX-License-Identifier: Apache-2.0
"""Whole numbers within bounds, said with their unit, and real numbers that are not booleans."""

from __future__ import annotations

import pytest

from sim_mirror.validation import Invalid, is_number, whole


def test_a_whole_number_in_bounds_is_kept_and_none_is_the_default() -> None:
    assert whole(250, 0, (0, 1000), "lead_ms") == 250
    assert whole(None, 60, (1, 300), "since_s", "seconds") == 60


@pytest.mark.parametrize("value", [1001, -1, 2.5, True, "250"])
def test_anything_else_is_refused_with_its_unit_and_bounds(value: object) -> None:
    with pytest.raises(Invalid, match="lead_ms must be a whole number of milliseconds from 0 to 1000"):
        whole(value, 0, (0, 1000), "lead_ms")
    with pytest.raises(Invalid, match=r"^lines must be a whole number from 0 to 1000$"):
        whole(value, 0, (0, 1000), "lines", None)


def test_a_number_is_an_int_or_a_float_and_never_a_bool() -> None:
    assert is_number(1) and is_number(1.5) and not is_number(True) and not is_number("1")
