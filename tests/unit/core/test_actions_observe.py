# SPDX-License-Identifier: Apache-2.0
"""Observe mode: an agent walking through an app may look and scroll, but a tap, a long press or typing into what reads
like Delete, Save or Sign out is refused unless its call confirms it -- by ref or by point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.schema import OBSERVE_WORDS
from sim_mirror.connectors.base import HidEvent
from sim_mirror.core.actions import PAUSED, AgentActions
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.refusals import ObserveGuard
from sim_mirror.perception.model import Frame
from sim_mirror.perception.snapshot import Element, Snapshot
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import FakeConnector, FakeEngine, FakeInputPause
from sim_mirror.testing.rig import DeviceRig, scope

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code · walkthrough")
GUARD = ObserveGuard.of(True, OBSERVE_WORDS, confirmed=False)
CONFIRMED = ObserveGuard.of(True, OBSERVE_WORDS, confirmed=True)


def framed(kind: str, label: str, x: float, y: float, width: float, height: float, **more: Any) -> dict[str, Any]:
    return {"type": kind, "label": label, "frame": {"x": x, "y": y, "width": width, "height": height}, **more}


#: An account screen: a row that only opens a list, a field, and what signs out and deletes -- one under a tab bar.
ACCOUNT = {
    "elements": [
        framed("Application", "Account", 0, 0, 402, 874, children=[
            framed("Button", "Saved items", 16, 100, 370, 44),
            framed("TextField", "Display name", 16, 160, 370, 44),
            framed("Button", "Sign Out", 16, 220, 370, 44),
            framed("Cell", "Danger zone", 0, 280, 402, 120, children=[
                framed("Button", "Delete account", 280, 330, 100, 44),
            ]),
            framed("Button", "Remove device", 16, 800, 370, 44),
            framed("Group", "Tab Bar", 0, 790, 402, 84, traits=["TabBar"], children=[
                framed("Button", "Home", 154, 795, 94, 54),
            ]),
        ]),
    ]
}  # fmt: skip


class Rigged:
    def __init__(self, rig: DeviceRig, instance: DeviceInstance) -> None:
        self.rig, self.instance = rig, instance
        self.actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=self.sleep)

    async def sleep(self, seconds: float) -> None:
        self.rig.clock.now += seconds
        await asyncio.sleep(0)

    @property
    def engine(self) -> FakeEngine:
        return self.rig.idb.engine

    async def act(self, steps: list[dict[str, Any]], guard: ObserveGuard | None = GUARD) -> list[str]:
        said = await self.actions.act(self.instance, CALLER, steps, snapshot="none", guard=guard)
        return said.splitlines()


async def rigged(folder: Path) -> Rigged:
    folder.mkdir(parents=True, exist_ok=True)
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=FakeEngine(document=ACCOUNT)))
    rigged = Rigged(rig, await rig.up())
    look = await rigged.actions.snapshot(rigged.instance, CALLER, mode="full", max_elements=120)
    assert look.splitlines()[1:] == [
        'e1 button "Saved items" (201,122)',
        'e2 field "Display name" (201,182)',
        'e3 button "Sign Out" (201,242)',
        'e4 cell "Danger zone" (201,340)',
        'e5 button "Delete account" (330,352)',
        'e6 button "Remove device" (201,822) covered by tab bar',
        'e7 button "Home" (201,822)',
    ]
    return rigged


def touched(events: list[HidEvent]) -> list[tuple[int, int]]:
    return [(round(event.x), round(event.y)) for event in events if event.phase == "down"]


async def test_a_tap_on_what_signs_out_is_refused_and_one_on_what_only_looks_alike_lands(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    assert await r.act([{"tap": "e1"}, {"tap": "e3"}, {"tap": "e2"}]) == [
        'ok tap e1 "Saved items" (201,122)',
        'error step 2: e3 "Sign Out" (201,242) looks like it changes something ("sign out"), and agents only observe '
        "here (agent.observe): it was not touched. If the person you work for asked for exactly this, call again with "
        "confirm: true",
    ]
    assert touched(r.engine.hid_events) == [(201, 122)]


async def test_a_tap_by_point_is_refused_on_what_is_under_it(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    said = await r.act([{"tap": [20, 300]}, {"tap": [330, 350]}])
    assert said == [
        "ok tap (20,300)",
        'error step 2: (330,350) on "Delete account" looks like it changes something ("delete"), and agents only '
        "observe here (agent.observe): it was not touched. If the person you work for asked for exactly this, call "
        "again with confirm: true",
    ]
    # What covers an element takes the touch: a point on the tab bar is a tap on the bar, not on what is under it.
    assert await r.act([{"tap": [201, 822]}]) == ["ok tap (201,822)"]


async def test_a_long_press_or_typing_into_it_is_refused_and_a_swipe_from_it_is_not(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    for step in ({"long_press": "e5"}, {"type": "x", "into": "e3"}, {"type": "x", "into": [201, 242]}):
        said = await r.act([step])
        assert said[0].startswith("error step 1: ") and "looks like it changes something" in said[0], step
    assert r.engine.hid_events == []
    assert (await r.act([{"swipe": {"from": "e5", "direction": "up"}}]))[0].startswith("ok swipe e5")
    assert (await r.act([{"type": "Ann", "into": "e2"}]))[0].startswith('ok type "Ann" into e2')


async def test_a_call_that_confirms_plays_it_and_says_so(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    assert await r.act([{"tap": "e5"}], CONFIRMED) == ['ok tap e5 "Delete account" (330,352), confirmed ("delete")']
    assert await r.act([{"tap": [20, 300]}], CONFIRMED) == ["ok tap (20,300)"]
    assert touched(r.engine.hid_events) == [(330, 352), (20, 300)]


async def test_a_point_after_a_step_is_checked_against_the_screen_as_it_is_now(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    moved = {"elements": [framed("Application", "Account", 0, 0, 402, 874, children=[
        framed("Button", "Erase All Content", 16, 100, 370, 44),
    ])]}  # fmt: skip
    r.engine.document = moved
    said = await r.act([{"tap": [201, 500]}, {"tap": [201, 122]}])
    assert said[0] == "ok tap (201,500)"
    assert said[1].startswith('error step 2: (201,122) on "Erase All Content" looks like it changes something')


async def test_without_observe_mode_nothing_is_refused(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    assert await r.act([{"tap": "e3"}, {"tap": [330, 350]}], guard=None) == [
        'ok tap e3 "Sign Out" (201,242)',
        "ok tap (330,350)",
    ]


@pytest.mark.parametrize("words", ["", " , "])
async def test_observe_mode_with_no_words_refuses_nothing(tmp_path: Path, words: str) -> None:
    r = await rigged(tmp_path)
    assert (await r.act([{"tap": "e3"}], ObserveGuard.of(True, words, confirmed=False)))[0].startswith("ok tap e3")


def test_an_element_read_without_its_edges_is_under_a_touch_near_its_point() -> None:
    edgeless = Element("e1", "button", "Delete", None, 100, 100)
    framed_one = Element("e2", "cell", "Row", None, 100, 100, frame=Frame(0, 80, 402, 40))
    heading = Element(None, "heading", "Account", None, 100, 100, frame=Frame(0, 0, 402, 200))
    look = Snapshot("iOS", (heading, framed_one, edgeless), "d", {}, 3)
    assert look.at(110, 115) == [edgeless, framed_one]
    assert look.at(130, 100) == [framed_one]
    assert look.at(500, 500) == []


@pytest.mark.parametrize(
    ("second", "why"),
    [
        # A covered ref is refused first, whatever its label says.
        ({"tap": "e6"}, "is under the tab bar"),
        # Then observe mode, before the pause a person made while the batch ran.
        ({"tap": "e3"}, "looks like it changes something"),
        ({"type": "x", "into": "e3"}, "looks like it changes something"),
        ({"tap": [330, 350]}, "looks like it changes something"),
        # And only a touch both let through is held by the pause.
        ({"tap": "e1"}, PAUSED),
    ],
)
async def test_a_refused_step_sends_nothing_and_refusals_come_covered_then_observed_then_paused(
    tmp_path: Path, second: dict[str, Any], why: str
) -> None:
    r = await rigged(tmp_path)
    pause = FakeInputPause()

    async def paused_after(seconds: float) -> None:
        pause.held.add(r.instance.udid)
        await r.sleep(seconds)

    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=paused_after, pause=pause)
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    events = r.instance.events.subscribe()
    pasted = len([args for args in r.rig.argv() if "pbcopy" in args])
    said = (await actions.act(r.instance, CALLER, [{"tap": "e2"}, second], snapshot="none", guard=GUARD)).splitlines()
    assert said[0].startswith("ok tap e2") and said[1].startswith("error step 2:") and why in said[1], said
    assert touched(r.engine.hid_events) == [(201, 182)], "only the first step touched the device"
    assert len([args for args in r.rig.argv() if "pbcopy" in args]) == pasted, "nothing was pasted"
    published = [events.get_nowait() for _ in range(events.qsize())]
    intents = [event for event in published if event["type"] == "agent" and event.get("phase") == "intent"]
    assert [event["gesture"]["kind"] for event in intents] == ["tap"], "the refused step was never announced"
