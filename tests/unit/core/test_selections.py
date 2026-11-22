# SPDX-License-Identifier: Apache-2.0
"""Which device each agent works on: bound at its choose or its first call, live while it calls, the first bound one
working on a device, told of another at most every two minutes, and forgotten when its connection ends."""

from __future__ import annotations

from sim_mirror.core.selections import IDLE_S, TELL_EVERY_S, Selections
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import ManualClock, StaticConfig
from sim_mirror.testing.rig import scope

A = Caller(scope("tp-1"), key="agent-a", title="Claude Code #aaaa")
B = Caller(scope("tp-1"), key="agent-b", title="Codex #bbbb")
ELSEWHERE = Caller(scope("tp-2"), key="agent-c", title="Codex #cccc")
X, Y = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def test_an_agent_is_bound_once_keeps_its_place_and_is_live_while_it_calls() -> None:
    clock = ManualClock()
    selections = Selections(clock=clock)
    assert selections.of(A) is None and not selections.is_live("agent-a")
    selections.begin(A)
    selections.bind(A, X)
    assert selections.of(A) == X and selections.is_live("agent-a")
    assert selections.of(Caller(scope("tp-2"), key="agent-a", title="")) is None, "only in its own scope"
    clock.now += 50
    selections.bind(A, X, chosen=True)
    (agent,) = selections.on(X)
    assert agent.since == 100 and agent.chosen, "bound again to the one it has: it keeps its place"
    clock.now += IDLE_S
    assert selections.is_live("agent-a"), "a call under way keeps it live however long it runs"
    selections.end(A)
    selections.end(Caller(scope("tp-1"), key="nobody", title=""))
    clock.now += IDLE_S - 1
    assert selections.is_live("agent-a") and selections.quiet_s(agent) == IDLE_S - 1
    clock.now += 1
    assert not selections.is_live("agent-a") and selections.on(X) == [], "quiet too long: not live"
    selections.unbind(A)
    selections.unbind(B)
    assert selections.of(A) is None


def test_the_first_agent_bound_works_on_a_device_and_one_that_came_later_is_told() -> None:
    clock = ManualClock()
    selections = Selections(StaticConfig(agent_idle_minutes=5), clock=clock)
    for caller in (A, B, ELSEWHERE):
        selections.begin(caller)
        selections.end(caller)
    selections.bind(A, X)
    clock.now += 1
    selections.bind(B, X)
    selections.bind(ELSEWHERE, Y)
    assert selections.occupant(B, X) is not None and selections.occupant(B, X).key == "agent-a"  # type: ignore[union-attr]
    assert selections.occupant(A, X) is None, "it came first"
    assert selections.occupant(ELSEWHERE, X).key == "agent-a"  # type: ignore[union-attr]
    assert [agent.key for agent in selections.others(A)] == ["agent-b"], "only its own scope's"
    assert selections.bound_to("tp-1", X) and not selections.bound_to("tp-2", X)
    clock.now += 5 * 60
    assert selections.occupant(B, X) is None and not selections.bound_to("tp-1", X), "past its scope's idle minutes"


def test_an_agent_is_told_of_another_at_most_every_two_minutes_and_forgetting_it_forgets_what_it_was_told() -> None:
    clock = ManualClock()
    selections = Selections(clock=clock)
    selections.begin(A)
    selections.bind(A, X)
    (other,) = selections.on(X)
    assert selections.tell(B, other) and not selections.tell(B, other)
    clock.now += TELL_EVERY_S
    assert selections.tell(B, other)
    forgotten = selections.forget("agent-a")
    assert forgotten is not None and forgotten.udid == X and selections.forget("agent-a") is None
    assert selections.tell(B, other), "told again once it is forgotten"


def test_a_person_moving_the_scopes_device_moves_the_agents_on_the_old_one() -> None:
    selections = Selections(clock=ManualClock())
    for caller, udid in ((A, X), (B, Y), (ELSEWHERE, X)):
        selections.begin(caller)
        selections.bind(caller, udid, chosen=True)
    selections.follow("tp-1", X, Y)
    assert selections.of(A) == Y and selections.of(B) == Y and selections.of(ELSEWHERE) == X
    assert [agent.chosen for agent in selections.on(Y)] == [False, True]
