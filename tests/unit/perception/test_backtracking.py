# SPDX-License-Identifier: Apache-2.0
"""Regexes that could hold the daemon up are named before a wait runs one: each refused shape, why it is slow, and the
patterns waits are written with let through."""

from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import Any

import pytest

from sim_mirror.perception import backtracking
from sim_mirror.perception.backtracking import ALIKE, NESTED, STACKED, hazard

#: What a parser given here says a repeat with no bound matches at most.
MAXREPEAT = 2**32 - 1


@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        # A run of a's can be split between the inner and the outer repeat in 2^n ways, and on a label that does not
        # end where the pattern says, every one of them is tried: 30 a's and a full stop take minutes.
        (r"(a+)+$", NESTED),
        (r"(a*)*b", NESTED),
        # The usual way it is written by accident: words and the space after them, the space optional.
        (r"^(\w+\s?)*$", NESTED),
        # Lazy or bounded at a hundred, the inner repeat still splits a run every way there is.
        (r"(a+?)+$", NESTED),
        (r"(a{1,100}){2,}$", NESTED),
        # Inside a lookahead, or a part matched only when a group did, it runs just the same.
        (r"(?=(a+)+$)", NESTED),
        (r"(a)?(?(1)(b+)+|c)$", NESTED),
        # Each a can be the first alternative, or the start of the second: a run of them can be taken in as many ways
        # as there are Fibonacci numbers below its length.
        (r"(a|aa)+$", ALIKE),
        # Any character but a, or é: é can be either, so a run of é is taken 2^n ways.
        (r"(?:[^a]|é)+$", ALIKE),
        # Two alternatives that both take a, inside what is repeated: each repeat takes it either way.
        (r"((a|a)b)+$", ALIKE),
        # Three repeats that can each take any of a run: n^4 ways to split n characters, so 1,000 take minutes.
        (r".*.*.*x", STACKED),
        (r"(.*)(.*)(.*)x", STACKED),
        # What stands between them matches the same character, or nothing: they still run together.
        (r".*a.*a.*b", STACKED),
        (r"\w+\s*\w+\s*\w+$", STACKED),
        (r"(?:ab)*(?:ab)*(?:ab)*c", STACKED),
        (r"[^a]*[^b]*[^c]*x", STACKED),
    ],
)
def test_a_regex_that_can_take_minutes_to_match_is_named_with_why(pattern: str, why: str) -> None:
    re.compile(pattern)
    assert hazard(pattern, ignore_case=False) == why
    assert hazard(pattern, ignore_case=True) == why


@pytest.mark.parametrize(
    "pattern",
    [
        "^Locked$",
        "^Lock(ed|ing)$",
        r"^(Lock|Unlock)ed\s*$",
        # The parser makes these one set of characters, or takes the common part out first: nothing to split.
        r"(\w|\d)*$",
        r"(a|ab)+$",
        r"(\s|\S)+$",
        r"([a-z]|[0-9])+$",
        r"(foo|bar)+$",
        r"(?:x|é)+$",
        # A repeat that holds only bounded repeats, or none.
        r"(\d{1,3}\.){3}\d{1,3}",
        r"(?:\d+,)?\d+",
        r"(?:Lock)+",
        # Repeats that cannot take the same character, or not three in a row.
        r"^\s*(\d+)\s*items?\s*$",
        r"\d+ of \d+",
        r".*Lock.*ed.*",
        r"\w+\s+\w+\s+\w+",
        r"\s*\w+\s*\d+\s*$",
        r"(?<=a)b+",
        r"(a)(?(1)b|c)+",
        r"(a)\1+",
    ],
)
def test_the_regexes_waits_are_written_with_are_let_through(pattern: str) -> None:
    re.compile(pattern)
    assert hazard(pattern, ignore_case=False) is None
    assert hazard(pattern, ignore_case=True) is None


def test_case_decides_whether_alternatives_begin_alike() -> None:
    # The parser keeps a and A apart; ignoring case, both take either.
    assert hazard(r"(?:a\d|A\w)+$", ignore_case=False) is None
    assert hazard(r"(?:a\d|A\w)+$", ignore_case=True) == ALIKE


def test_each_refused_shape_is_slow_where_a_safe_one_is_not() -> None:
    """Why they are refused, measured: on a label of 20 characters, or 150, each refused shape already takes many times
    longer than a pattern that matches the same labels without it -- and doubles, or more, with every character."""

    def took(pattern: str, text: str) -> float:
        """The quickest of a few searches: a pause of the machine's own lengthens one, never all."""
        compiled = re.compile(pattern)
        times = []
        for _ in range(3):
            began = time.perf_counter()
            compiled.search(text)
            times.append(time.perf_counter() - began)
        return min(times)

    assert took(r"(a+)+$", "a" * 18 + "!") > 20 * took(r"a+$", "a" * 18 + "!")
    assert took(r"(a|aa)+$", "a" * 24 + "!") > 20 * took(r"a+$", "a" * 24 + "!")
    assert took(r".*.*.*x", "a" * 150) > 20 * took(r".*x", "a" * 150)


def test_a_python_without_the_parser_or_with_one_read_otherwise_lets_the_pattern_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtracking, "PARSERS", ("sim_mirror.no_such_parser", *backtracking.PARSERS))
    assert backtracking.parser() is not None and hazard(r"(a+)+$", ignore_case=False) == NESTED
    monkeypatch.setattr(backtracking, "PARSERS", ("sim_mirror.no_such_parser",))
    assert backtracking.parser() is None and hazard(r"(a+)+$", ignore_case=False) is None

    def unknown(pattern: str, flags: int) -> Any:
        return [("IN", [("CATEGORY", "CATEGORY_UNHEARD_OF")])]

    monkeypatch.setattr(backtracking, "parser", lambda: SimpleNamespace(parse=unknown, MAXREPEAT=MAXREPEAT))
    assert hazard(r"\d", ignore_case=False) is None


def test_atomic_groups_are_read_like_any_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """``(?>…)`` is read from Python 3.11; the tree is given here, as Python 3.10's parser cannot read it."""
    a_plus = ("MAX_REPEAT", (1, MAXREPEAT, [("LITERAL", 97)]))
    anything = ("ATOMIC_GROUP", [("MAX_REPEAT", (0, MAXREPEAT, [("ANY", None)]))])
    trees = {
        "(?>a+)+$": [("MAX_REPEAT", (1, MAXREPEAT, [("ATOMIC_GROUP", [a_plus])]))],
        "(?>.*)(?>.*)(?>.*)x": [anything, anything, anything, ("LITERAL", 120)],
        "(?>a)+": [("MAX_REPEAT", (1, MAXREPEAT, [("ATOMIC_GROUP", [("LITERAL", 97)])]))],
    }
    monkeypatch.setattr(
        backtracking,
        "parser",
        lambda: SimpleNamespace(parse=lambda pattern, flags: trees[pattern], MAXREPEAT=MAXREPEAT),
    )
    assert hazard("(?>a+)+$", ignore_case=False) == NESTED
    assert hazard("(?>.*)(?>.*)(?>.*)x", ignore_case=False) == STACKED
    assert hazard("(?>a)+", ignore_case=False) is None
