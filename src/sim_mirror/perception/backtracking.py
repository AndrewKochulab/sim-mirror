# SPDX-License-Identifier: Apache-2.0
"""Regular expressions that could hold the daemon up, named before a wait runs one.

A wait's regex is searched in every label on the screen, read after read, on the event loop every agent's calls are
answered on. Python's `re` backtracks: a pattern that can match the same text in many ways tries each of them on a
label it does not match, and ``(a+)+$`` takes minutes on forty a's and a full stop -- with nothing else answered
meanwhile. `hazard` reads a pattern with the standard library's own parser and names the shapes that do that:

- **nested repeats**: a part repeated more than once that holds a repeat with no bound -- ``(a+)+``, ``(\\w+\\s?)*`` --
  as each run of text can be split between the two repeats in every way there is;
- **alternatives that begin alike, repeated**: ``(a|ab)+``, ``(\\w|\\d)*`` -- each character can be taken by either
  alternative, so a run of them can be taken in twice as many ways as one character shorter;
- **stacked repeats**: more than `STACKED_MAX` unbounded repeats in a row that can all match the same character --
  ``.*.*.*x`` -- whose time grows with a label's length to the power of how many there are, plus one.

A repeat counts as unbounded past `LONG_REPEAT` times, as ``.{0,1000}`` is as slow as ``.*``. Whether two parts can
match the same character is judged on the characters the pattern names and on ASCII, which is where it matters.

It reads the parser's tree, which is not a public interface: a Python whose parser cannot be found, or reads in a way
this does not know, has a pattern let through rather than refused -- the text a regex is searched in is cut as well
(`snapshot.REGEX_TEXT_MAX`), so what slips by is bounded.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Iterator, Sequence
from importlib import import_module
from types import ModuleType
from typing import Any

#: Where the standard library keeps its regex parser: ``re._parser`` from Python 3.11, ``sre_parse`` before.
PARSERS = ("re._parser", "sre_parse")
#: How many unbounded repeats that can match the same character may stand in a row.
STACKED_MAX = 2
#: How many times a repeat may match before it counts as unbounded.
LONG_REPEAT = 50

NESTED = "nested repeats can take minutes to match; repeat the inner part only"
ALIKE = "a repeat of alternatives that can begin with the same character can take minutes to match; make them differ"
STACKED = (
    f"more than {STACKED_MAX} repeats in a row that can match the same character can take minutes to match; put text "
    "only one of them matches between them"
)

REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})
#: What matches one character.
ATOMS = frozenset({"LITERAL", "NOT_LITERAL", "ANY", "IN"})
#: What matches no text: where it is, or what is around it.
ZERO_WIDTH = frozenset({"AT", "ASSERT", "ASSERT_NOT"})
#: A class of characters, as a pattern says it.
CATEGORIES = {
    "CATEGORY_DIGIT": r"\d", "CATEGORY_NOT_DIGIT": r"\D", "CATEGORY_SPACE": r"\s", "CATEGORY_NOT_SPACE": r"\S",
    "CATEGORY_WORD": r"\w", "CATEGORY_NOT_WORD": r"\W",
}  # fmt: skip
#: Characters past ASCII that classes treat differently, so they are told apart too.
BEYOND_ASCII = "é Ж中٣ "

Chars = frozenset[str]
Items = Sequence[tuple[Any, Any]]
NONE: Chars = frozenset()


class _Refused(Exception):
    pass


def parser() -> ModuleType | None:
    """The standard library's regex parser, or None when this Python keeps it nowhere known."""
    for name in PARSERS:
        try:
            return import_module(name)
        except ImportError:
            continue
    return None


def hazard(pattern: str, *, ignore_case: bool) -> str | None:
    """Why searching with `pattern` could take minutes, or None when nothing in it can -- or it cannot be read. The
    pattern compiles."""
    found = parser()
    if found is None:
        return None
    try:
        tree = found.parse(pattern, re.IGNORECASE if ignore_case else 0)
        _Reader(tree, found.MAXREPEAT, ignore_case).check(tree, NONE, repeated=False)
    except _Refused as exc:
        return str(exc)
    except Exception:
        # A parser that reads otherwise than this knows: the cut text is the guard left.
        return None
    return None


class _Reader:
    """One pattern's tree: which characters each part can begin with, whether it can match nothing, and its
    hazards."""

    def __init__(self, tree: Items, unbounded: int, ignore_case: bool) -> None:
        self._unbounded = unbounded
        self._fold = ignore_case
        named = {chr(code) for code in _codes(tree)}
        self._alphabet: Chars = self._folded({chr(code) for code in range(128)} | set(BEYOND_ASCII) | named)

    def _folded(self, chars: set[str] | Chars) -> Chars:
        return frozenset(char.lower() for char in chars) if self._fold else frozenset(chars)

    def _long(self, most: int) -> bool:
        return most >= self._unbounded or most > LONG_REPEAT

    # -- what a part matches first ----------------------------------------------------------------------------------

    def _atom(self, name: str, value: Any) -> Chars:
        if name == "LITERAL":
            return self._folded({chr(value)})
        if name == "NOT_LITERAL":
            return self._alphabet - self._folded({chr(value)})
        if name == "ANY":
            return self._alphabet
        negated = any(str(op) == "NEGATE" for op, _ in value)
        chars: set[str] = set()
        for op, part in value:
            kind = str(op)
            if kind == "LITERAL":
                chars.add(chr(part))
            elif kind == "RANGE":
                low, high = part
                chars |= {char for char in self._alphabet if low <= ord(char) <= high} | {chr(low), chr(high)}
            elif kind == "CATEGORY":
                said = CATEGORIES[str(part)]
                chars |= {char for char in self._alphabet if re.fullmatch(said, char)}
        found = self._folded(chars)
        return self._alphabet - found if negated else found

    def _item(self, op: Any, value: Any) -> tuple[Chars, bool]:
        """The characters a part can begin with, and whether it can match no text."""
        name = str(op)
        if name in ATOMS:
            return self._atom(name, value), False
        if name in REPEATS:
            least, _most, inner = value
            first, empty = self._sequence(inner)
            return first, empty or least == 0
        if name == "BRANCH":
            read = [self._sequence(branch) for branch in value[1]]
            return frozenset().union(*(first for first, _ in read)), any(empty for _, empty in read)
        if name in ZERO_WIDTH:
            return NONE, True
        if name in ("SUBPATTERN", "ATOMIC_GROUP"):
            return self._sequence(_inner(name, value)[0])
        if name == "GROUPREF_EXISTS":
            read = [self._sequence(part) for part in _inner(name, value)]
            first = frozenset().union(*(first for first, _ in read))
            # Without a "no" part, it matches nothing when the group did not match.
            return first, len(read) < 2 or any(empty for _, empty in read)
        # A backreference: anything, or nothing.
        return self._alphabet, True

    def _sequence(self, items: Items) -> tuple[Chars, bool]:
        found: set[str] = set()
        for op, value in items:
            first, empty = self._item(op, value)
            found |= first
            if not empty:
                return frozenset(found), False
        return frozenset(found), True

    # -- hazards ----------------------------------------------------------------------------------------------------

    def check(self, items: Items, follow: Chars, *, repeated: bool) -> None:
        """Raise `_Refused` for a hazard in `items`, which `follow` can come after; `repeated` when they are matched
        more than once in a row."""
        self._stacked(items)
        # What can come after each part: the next part's first characters, and on past those that can match nothing.
        after = [follow] * len(items)
        for index in range(len(items) - 1, 0, -1):
            first, empty = self._item(*items[index])
            after[index - 1] = first | (after[index] if empty else NONE)
        for (op, value), then in zip(items, after, strict=True):
            name = str(op)
            if name in REPEATS:
                _least, most, inner = value
                many = most > 1
                if many and any(self._long(inner_most) for inner_most in _repeats(inner)):
                    raise _Refused(NESTED)
                self.check(inner, self._sequence(inner)[0] | then if many else then, repeated=repeated or many)
                continue
            if name == "BRANCH" and repeated:
                starts = [first | (then if empty else NONE) for first, empty in map(self._sequence, value[1])]
                if any(one & other for one, other in itertools.combinations(starts, 2)):
                    raise _Refused(ALIKE)
            for part in _inner(name, value):
                self.check(part, NONE if name in ZERO_WIDTH else then, repeated=repeated)

    def _stacked(self, items: Items) -> None:
        """Refuse more than `STACKED_MAX` unbounded repeats in a row that can all match one character -- what stands
        between them matching it too, or nothing.

        Each run of such repeats is followed at once -- by the characters all of its repeats can match, and how many
        there are -- as ``\\w+\\s*\\w+`` holds a run of ``\\w`` and one of ``\\s`` that each go on past the other."""
        runs: dict[Chars, int] = {}
        for op, value in _flat(items):
            first, empty = self._item(op, value)
            if str(op) not in REPEATS or not self._long(value[1]):
                if not empty:
                    runs = _kept((common & first, count) for common, count in runs.items())
                continue
            grown = [(common & first, count + 1) for common, count in runs.items() if common & first]
            # A repeat that can match nothing lets each run go on past it uncounted.
            passed = list(runs.items()) if empty else []
            runs = _kept([*grown, *passed, (first, 1)])
            if max(runs.values()) > STACKED_MAX:
                raise _Refused(STACKED)


def _kept(found: Iterable[tuple[Chars, int]]) -> dict[Chars, int]:
    """The runs that can still match a character, each with the most repeats it has had."""
    kept: dict[Chars, int] = {}
    for common, count in found:
        if common:
            kept[common] = max(count, kept.get(common, 0))
    return kept


def _inner(name: str, value: Any) -> list[Items]:
    """The sequences a part holds."""
    if name in REPEATS:
        return [value[2]]
    if name == "SUBPATTERN":
        return [value[-1]]
    if name == "ATOMIC_GROUP":
        return [value]
    if name == "BRANCH":
        return list(value[1])
    if name in ("ASSERT", "ASSERT_NOT"):
        return [value[1]]
    if name == "GROUPREF_EXISTS":
        return [part for part in value[1:] if part is not None]
    return []


def _flat(items: Items) -> Iterator[tuple[Any, Any]]:
    """The parts of a sequence, with the groups in it opened: ``(.*)(.*)`` is two repeats in a row."""
    for op, value in items:
        name = str(op)
        if name in ("SUBPATTERN", "ATOMIC_GROUP"):
            yield from _flat(_inner(name, value)[0])
        else:
            yield op, value


def _repeats(items: Items) -> Iterator[int]:
    """How many times at most each repeat in `items` matches, however deep."""
    for op, value in items:
        name = str(op)
        if name in REPEATS:
            yield value[1]
        for part in _inner(name, value):
            yield from _repeats(part)


def _codes(items: Items) -> Iterator[int]:
    """Every character a pattern names, however deep: in a literal, a set or a range."""
    for op, value in items:
        name = str(op)
        if name in ("LITERAL", "NOT_LITERAL"):
            yield value
        elif name == "IN":
            for kind, part in value:
                if str(kind) == "LITERAL":
                    yield part
                elif str(kind) == "RANGE":
                    yield from part
        for inner in _inner(name, value):
            yield from _codes(inner)
