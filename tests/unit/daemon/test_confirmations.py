# SPDX-License-Identifier: Apache-2.0
"""Sensitive settings changes held for a person at the terminal: one code per change, bound to it, spent once, dropped
after wrong guesses or when it expires, and only a few waiting at once."""

from __future__ import annotations

import pytest

from sim_mirror.daemon import confirmations
from sim_mirror.daemon.confirmations import (
    ALPHABET,
    ATTEMPTS,
    CONFIRM_TTL_S,
    WAITING_MAX,
    PendingChanges,
    normalized,
)
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import ManualClock

DEMO = Scope.named("demo")


def code_of(pending: PendingChanges, index: int = 0) -> str:
    return str(pending.waiting()[index]["code"])


def test_a_change_waits_with_one_code_and_asking_again_for_it_answers_the_same() -> None:
    pending = PendingChanges(clock=ManualClock())
    first = pending.request(DEMO, "digest-a", "demo: build.tools = true")
    assert pending.request(DEMO, "digest-a", "demo: build.tools = true") == first
    assert first["command"] == "sim-mirror settings confirm" and first["expires_in_s"] == CONFIRM_TTL_S
    (shown,) = pending.waiting()
    assert shown["scope"] == "demo" and shown["summary"] == "demo: build.tools = true" and shown["id"] == first["id"]
    code = str(shown["code"])
    assert len(code) == 9 and code[4] == "-" and set(code.replace("-", "")) <= set(ALPHABET)


def test_a_code_confirms_its_own_change_once_however_it_was_typed_and_no_other_change() -> None:
    pending = PendingChanges(clock=ManualClock())
    pending.request(DEMO, "digest-a", "a")
    pending.request(DEMO, "digest-b", "b")
    code_a = code_of(pending, 0)
    assert not pending.confirm("digest-b", code_a)
    assert not pending.confirm("digest-missing", code_a)
    assert pending.confirm("digest-a", f"  {code_a.lower()} ")
    assert not pending.confirm("digest-a", code_a)
    assert [held["summary"] for held in pending.waiting()] == ["b"]
    assert normalized("ab-cd ef") == "ABCDEF"


def test_a_change_is_dropped_after_too_many_wrong_codes_or_once_it_expires() -> None:
    clock = ManualClock()
    pending = PendingChanges(clock=clock)
    pending.request(DEMO, "digest-a", "a")
    code = code_of(pending)
    for _ in range(ATTEMPTS - 1):
        assert not pending.confirm("digest-a", "WRONG")
    assert pending.waiting() != []
    assert not pending.confirm("digest-a", "WRONG") and pending.waiting() == []
    assert not pending.confirm("digest-a", code), "a change dropped for guessing is not confirmed by its old code"
    pending.request(DEMO, "digest-b", "b")
    clock.advance(CONFIRM_TTL_S)
    assert pending.waiting() == [] and not pending.confirm("digest-b", "ANY")


def test_only_a_few_changes_wait_at_once_and_the_oldest_goes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = ManualClock()
    pending = PendingChanges(clock=clock)
    for index in range(WAITING_MAX + 2):
        pending.request(DEMO, f"digest-{index}", f"change {index}")
        clock.advance(1)
    summaries = [held["summary"] for held in pending.waiting()]
    assert len(summaries) == WAITING_MAX and summaries[0] == "change 2" and summaries[-1] == f"change {WAITING_MAX + 1}"
    monkeypatch.setattr(confirmations.secrets, "choice", lambda alphabet: "Z")
    fresh = PendingChanges(clock=clock)
    fresh.request(DEMO, "d", "x")
    assert fresh.waiting()[0]["code"] == "ZZZZ-ZZZZ"
