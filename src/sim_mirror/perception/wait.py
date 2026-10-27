# SPDX-License-Identifier: Apache-2.0
"""Waiting after an agent's steps: for text to appear, for it to go, for it to hold, or for the screen to settle.

A wait is asked for as one of ``{"for": text}``, ``{"gone": text}``, ``{"stable": {"for": text, "ms": ms}}`` or
``{"settle_ms": ms}``, with an optional ``timeout_ms``. The text is a string -- found in a label or a value, ignoring
case, as it always was -- or an object that says how to match it (`parse_match`): ``{"exact": "Locked"}`` for the whole
label or value with its case, so a wait for Locked is not met by Unlocked; ``{"regex": "^Lock(ed|ing)$"}`` -- one that
could take minutes to match, as ``(a+)+`` can, is refused (`perception.backtracking`); or ``{"text": "Locked", "case":
true}``. ``stable`` is met once what it looks for has been on screen -- or, with ``gone``,
off it -- through every read for ``ms``: a lock that has stopped moving, not one passing through.

`parse_wait` checks a wait -- one asked for wrongly is skipped with why, never guessed at, and a key it does not take
is refused rather than dropped -- and `Waiter` runs it, answering one line: when what it waited for was seen, or that
it gave up.

**When is said as a window, not a single number.** Each read of the screen takes time -- a real device's can take a
second -- and what it saw was on screen at some moment while it ran. So a met wait says when the read that saw it
began and ended, after the last step: ``seen in a read 410-1180ms after the last step``. Both are this Mac's clock,
not the device's.

**A read that fails is tried again** until the wait's time is up, as a screen that is being rebuilt cannot be read for a
moment; only one that never reads again ends the wait, saying why.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from sim_mirror.perception.backtracking import hazard
from sim_mirror.perception.settle import POLL_S, SettleDetector
from sim_mirror.perception.snapshot import Match, MatchHow, Snapshot
from sim_mirror.validation import Invalid, whole

TIMEOUT_MS = (100, 120000)
DEFAULT_TIMEOUT_MS = 5000
SETTLE_MS = (100, 3000)
#: How long a ``stable`` wait's text must hold, at most as long as the wait may run.
STABLE_MS = (100, TIMEOUT_MS[1])
#: The longest text and regular expression a wait looks for. A pattern is searched in every label on the screen, read
#: after read, so it is kept short.
TEXT_MAX = 500
REGEX_MAX = 200
SHAPE = 'give one of {"for": text}, {"gone": text}, {"stable": {"for": text, "ms": ms}} or {"settle_ms": ms}'
#: What a wait may hold beside its kind; anything else is refused, never ignored.
KEYS = ("for", "gone", "stable", "settle_ms", "timeout_ms")
STABLE_KEYS = ("for", "gone", "ms")
MATCH_KEYS: tuple[MatchHow, ...] = ("text", "exact", "regex")
MATCH_SHAPE = (
    'is text, found in a label or value ignoring case, or {"exact": text} for the whole label or value, '
    '{"regex": pattern}, or {"text": text, "case": true}'
)
#: Whether each way of matching minds case when the wait does not say: a string never has.
CASE_DEFAULT: dict[MatchHow, bool] = {"text": False, "exact": True, "regex": True}

Kind = Literal["for", "gone", "settle", "stable"]


@dataclass(frozen=True)
class Wait:
    kind: Kind
    timeout_s: float
    text: str = ""
    quiet_s: float = 0.0
    #: How what it waits for is matched; for a string, a part of a label or value, ignoring case.
    match: Match | None = field(default=None, compare=False)
    #: For ``stable``: whether what it holds is the text being there, rather than gone.
    present: bool = True
    #: For ``stable``: how long it must hold.
    hold_s: float = 0.0


@dataclass(frozen=True)
class Waited:
    """A wait's line, and whether it was met. A wait that gave up, or never read the screen, was not."""

    line: str
    met: bool


def _quoted(keys: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f'"{key}"' for key in keys)


def _refuse_unknown(spec: dict[object, object], allowed: tuple[str, ...], what: str) -> None:
    """Refuse a key `what` does not take, naming those it does: a key dropped silently is a wait never asked for."""
    extra = sorted(str(key) for key in spec if key not in allowed)
    if extra:
        raise Invalid(f"{what} does not take {_quoted(extra)}; it takes {_quoted(allowed)}")


def parse_match(value: object, name: str) -> Match:
    """What a wait's `name` (``for``, ``gone``) looks for. Raises `Invalid` with what would do."""
    if isinstance(value, str):
        if not value:
            raise Invalid("wait takes text to look for")
        if len(value) > TEXT_MAX:
            raise Invalid(f"{name} is at most {TEXT_MAX} characters")
        return Match.text(value)
    if not isinstance(value, dict):
        raise Invalid("wait takes text to look for")
    _refuse_unknown(value, (*MATCH_KEYS, "case"), f'"{name}"')
    hows = [how for how in MATCH_KEYS if how in value]
    if len(hows) != 1:
        raise Invalid(f'"{name}" {MATCH_SHAPE}')
    how = hows[0]
    wanted = value[how]
    limit = REGEX_MAX if how == "regex" else TEXT_MAX
    if not isinstance(wanted, str) or not 0 < len(wanted) <= limit:
        raise Invalid(f'"{how}" is text of 1 to {limit} characters')
    case = value.get("case", CASE_DEFAULT[how])
    if not isinstance(case, bool):
        raise Invalid('"case" is true or false')
    if how != "regex":
        return Match(how, wanted, case)
    try:
        pattern = re.compile(wanted, 0 if case else re.IGNORECASE)
    except re.error as exc:
        raise Invalid(f"the regex {wanted!r} does not compile: {exc}") from None
    # It runs on the loop every agent is answered on: one that could backtrack for minutes would hold them all.
    danger = hazard(wanted, ignore_case=not case)
    if danger is not None:
        raise Invalid(f"the regex {wanted!r} is refused: {danger}")
    return Match(how, wanted, case, pattern)


def parse_wait(spec: object) -> Wait:
    """The wait a call asked for. Raises `Invalid` with what would do."""
    if not isinstance(spec, dict) or sum(key in spec for key in ("for", "gone", "stable", "settle_ms")) != 1:
        raise Invalid(SHAPE)
    _refuse_unknown(spec, KEYS, "wait")
    given = spec.get("timeout_ms")
    timeout_s = whole(given, DEFAULT_TIMEOUT_MS, TIMEOUT_MS, "timeout_ms") / 1000
    if "settle_ms" in spec:
        return Wait("settle", timeout_s, quiet_s=whole(spec["settle_ms"], 0, SETTLE_MS, "settle_ms") / 1000)
    if "stable" in spec:
        return _stable(spec["stable"], given)
    kind: Literal["for", "gone"] = "for" if "for" in spec else "gone"
    match = parse_match(spec[kind], kind)
    return Wait(kind, timeout_s, text=match.wanted, match=match)


def _stable(stable: object, given: object) -> Wait:
    """A ``stable`` wait: what it looks for, and how long it must hold, within the wait's time -- which, not given, is
    that long and `DEFAULT_TIMEOUT_MS` more."""
    if not isinstance(stable, dict) or sum(key in stable for key in ("for", "gone")) != 1 or "ms" not in stable:
        raise Invalid('stable is {"for": text, "ms": ms}, or {"gone": text, "ms": ms}: text that holds that long')
    _refuse_unknown(stable, STABLE_KEYS, "stable")
    hold_ms = whole(stable["ms"], 0, STABLE_MS, "stable ms")
    default = min(hold_ms + DEFAULT_TIMEOUT_MS, TIMEOUT_MS[1])
    timeout_ms = whole(given, default, TIMEOUT_MS, "timeout_ms")
    if hold_ms > timeout_ms:
        raise Invalid(f"stable ms ({hold_ms}) must be within timeout_ms ({timeout_ms})")
    name: Literal["for", "gone"] = "for" if "for" in stable else "gone"
    match = parse_match(stable[name], name)
    return Wait(
        "stable", timeout_ms / 1000, text=match.wanted, match=match, present=name == "for", hold_s=hold_ms / 1000
    )


def _window(start: float, end: float) -> str:
    """A span after the last step, in milliseconds: one number when it took no time."""
    low, high = round(start * 1000), round(end * 1000)
    return f"{low}ms" if low == high else f"{low}-{high}ms"


class Waiter:
    def __init__(
        self,
        *,
        read: Callable[[], Awaitable[Snapshot]],
        settle: SettleDetector,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        unreadable: tuple[type[BaseException], ...] = (),
    ) -> None:
        """`unreadable` are the errors a read that could not read the screen raises: tried again until the wait's
        time is up."""
        self._read = read
        self._settle = settle
        self._clock = clock
        self._sleep = sleep
        self._unreadable = unreadable

    async def run(self, wait: Wait) -> Waited:
        if wait.kind == "settle":
            said = await self._settle.settle(wait.quiet_s, wait.timeout_s)
            return Waited(said, said.startswith("settled"))
        assert wait.match is not None, "a wait for text has a match"
        named = wait.match.describe()
        present = wait.present if wait.kind == "stable" else wait.kind == "for"
        hold_ms = round(wait.hold_s * 1000)
        what = named if present else f"{named} gone"
        started = self._clock()
        #: For ``stable``: the read that began the run of reads it has held through, as (began, ended).
        holding: tuple[float, float] | None = None
        longest = 0.0
        while True:
            began = self._clock()
            failure: BaseException | None = None
            try:
                found = (await self._read()).find(wait.match) is not None
            except self._unreadable as exc:
                failure, found = exc, not present
            ended = self._clock()
            after = (began - started, ended - started)
            if found != present:
                holding = None
            elif wait.kind != "stable":
                seen = "seen" if present else "gone"
                goes = "" if present else " to go"
                return Waited(f"waited for {named}{goes}: {seen} in a read {_window(*after)} after the last step", True)
            else:
                holding = holding or after
                # Held from the end of the first read that saw it to the start of this one, at the least.
                held = after[0] - holding[1]
                longest = max(longest, held)
                if held >= wait.hold_s:
                    return Waited(
                        f"waited for {what} to hold {hold_ms}ms: held from a read {_window(*holding)} after the last "
                        f"step to one at {_window(*after)}",
                        True,
                    )
            if after[1] >= wait.timeout_s:
                waited = round(after[1] * 1000)
                if failure is not None:
                    return Waited(f"wait stopped: the screen could not be read for {waited}ms: {failure}", False)
                if wait.kind == "stable":
                    return Waited(
                        f"{what} did not hold {hold_ms}ms within {waited}ms (held at most {round(longest * 1000)}ms)",
                        False,
                    )
                return Waited(f"still {'no' if present else 'showing'} {named} after {waited}ms", False)
            await self._sleep(POLL_S)
