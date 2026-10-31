# SPDX-License-Identifier: Apache-2.0
"""Observe mode: an agent walking through an app may look and scroll, but a tap, a long press or typing into what reads
like Delete, Save or Sign out is refused unless its call confirms it -- by ref or by point."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.schema import OBSERVE_WORDS
from sim_mirror.connectors.app.merge import AppHierarchyMerge
from sim_mirror.connectors.base import ConnectorError, HidEvent
from sim_mirror.core.actions import PAUSED, AgentActions
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.refusals import ObserveGuard
from sim_mirror.perception.model import Frame
from sim_mirror.perception.readers import CombinedExtraReaders
from sim_mirror.perception.snapshot import Element, Snapshot
from sim_mirror.platform.device_data import DEVICES_DIR_ENV
from sim_mirror.seams import Caller
from sim_mirror.testing.app_sdk import FakeAppSdk, write_listing
from sim_mirror.testing.fakes import FakeConnector, FakeEngine, FakeInputPause, fixture_json, point_key
from sim_mirror.testing.rig import DeviceRig, scope

#: What the app SDK's sample app shared, recorded: protocol/app-sdk/v1's examples.
APP_SDK_EXAMPLES = Path(__file__).resolve().parents[3] / "protocol" / "app-sdk" / "v1" / "examples"

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code · walkthrough")
GUARD = ObserveGuard.of(True, OBSERVE_WORDS, confirmed=False)
CONFIRMED = ObserveGuard.of(True, OBSERVE_WORDS, confirmed=True)


def untimed(answer: str) -> str:
    """An answer without when each step was done, for a test of what was done."""
    return re.sub(r" \+\d+ms$", "", answer, flags=re.MULTILINE)


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
        said = untimed(await self.actions.act(self.instance, CALLER, steps, snapshot="none", guard=guard))
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
        ({"swipe": {"from": "e6", "direction": "left"}}, "is under the tab bar"),
        ({"swipe": {"from": "e1", "direction": "left"}}, "a sideways swipe from e1"),
        ({"drag": {"path": [[201, 600], [330, 350]]}}, "it was not dragged"),
        ({"type": "x", "into": "e2", "submit": True}, "submit presses return"),
        ({"press": "return"}, "press return presses return"),
        # And only a touch both let through is held by the pause.
        ({"tap": "e1"}, PAUSED),
        ({"swipe": {"from": "e1", "direction": "up"}}, PAUSED),
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
    said = (
        untimed(await actions.act(r.instance, CALLER, [{"tap": "e2"}, second], snapshot="none", guard=GUARD))
    ).splitlines()
    assert said[0].startswith("ok tap e2") and said[1].startswith("error step 2:") and why in said[1], said
    assert touched(r.engine.hid_events) == [(201, 182)], "only the first step touched the device"
    assert len([args for args in r.rig.argv() if "pbcopy" in args]) == pasted, "nothing was pasted"
    published = [events.get_nowait() for _ in range(events.qsize())]
    intents = [event for event in published if event["type"] == "agent" and event.get("phase") == "intent"]
    assert [event["gesture"]["kind"] for event in intents] == ["tap"], "the refused step was never announced"


#: A list a row of which says it can be deleted, a card, an icon-only button, a drop target, a form and a search.
GROCERIES = {
    "elements": [
        framed("Application", "Groceries", 0, 0, 402, 874, children=[
            framed("SearchField", "Search", 16, 60, 370, 36, traits=["SearchField"]),
            framed("Cell", "Milk", 0, 100, 402, 52, custom_actions=["Delete"]),
            framed("Cell", "Bread", 0, 152, 402, 52, custom_actions=[]),
            framed("Button", "Daily mix", 51, 300, 300, 200),
            framed("TextField", "Note", 16, 600, 260, 44),
            framed("Button", "Send", 300, 600, 80, 44),
            framed("Button", "Clear", 16, 520, 80, 44, identifier="deleteAllButton"),
            framed("Image", "trash", 340, 700, 44, 44),
        ]),
    ]
}  # fmt: skip


async def on(folder: Path, document: dict[str, Any]) -> Rigged:
    folder.mkdir(parents=True, exist_ok=True)
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=FakeEngine(document=document)))
    return Rigged(rig, await rig.up())


async def groceries(folder: Path) -> Rigged:
    r = await on(folder, GROCERIES)
    look = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert look.splitlines()[1:] == [
        'e1 search "Search" (201,78)',
        'e2 cell "Milk" (201,126)',
        'e3 cell "Bread" (201,178)',
        'e4 button "Daily mix" (201,400)',
        'e5 field "Note" (146,622)',
        'e6 button "Send" (340,622)',
        'e7 button "Clear" (56,542)',
        'e8 image "trash" (362,722)',
    ]
    return r


async def test_a_sideways_swipe_on_a_row_is_refused_and_scrolling_or_going_back_is_not(tmp_path: Path) -> None:
    r = await groceries(tmp_path)
    assert await r.act([{"swipe": {"from": "e2", "direction": "left"}}]) == [
        'error step 1: a sideways swipe from e2 "Milk" (201,126) can act on it ("delete"), and agents only observe '
        "here (agent.observe): it was not swiped. To scroll, swipe up or down. If the person you work for asked for "
        "exactly this, call again with confirm: true"
    ]
    for swipe in ({"from": "e3", "direction": "right"}, {"from": [300, 178], "to": [20, 190]}):
        said = await r.act([{"swipe": swipe}])
        assert said[0].startswith("error step 1: a sideways swipe from ") and "can act on a row" in said[0], swipe
    assert r.engine.hid_events == []
    played = [
        {"swipe": {"from": "e2", "direction": "up"}},
        {"swipe": {"from": [5, 178], "direction": "right"}},
        {"swipe": {"from": "e4", "direction": "left"}},
    ]
    assert await r.act(played) == [
        'ok swipe e2 "Milk" (201,126) → (201,0)',
        "ok swipe (5,178) → (146,178)",
        'ok swipe e4 "Daily mix" (201,400) → (60,400)',
    ]
    assert await r.act([{"swipe": {"from": "e3", "direction": "left"}}], CONFIRMED) == [
        'ok swipe e3 "Bread" (201,178) → (60,178), confirmed ("a sideways swipe on a row")'
    ]


async def test_a_drag_that_swipes_a_row_or_drops_on_a_trash_can_is_refused(tmp_path: Path) -> None:
    r = await groceries(tmp_path)
    said = await r.act([{"drag": {"path": [[300, 126], [60, 130]]}}])
    assert said[0].startswith('error step 1: a sideways swipe from (300,126) on "Milk" can act on it ("delete")')
    assert await r.act([{"drag": {"path": [[201, 400], [362, 722]]}}]) == [
        'error step 1: a drag to (362,722) ends on "trash", which looks like it changes something ("trash"), and '
        "agents only observe here (agent.observe): it was not dragged. If the person you work for asked for exactly "
        "this, call again with confirm: true"
    ]
    assert r.engine.hid_events == []
    assert await r.act([{"drag": {"path": ["e4", [201, 120]]}}]) == ["ok drag (201,400) → (201,120) through 2 points"]
    confirmed = [{"drag": {"path": [[300, 126], [60, 130]]}}, {"drag": {"path": ["e4", "e8"]}}]
    assert await r.act(confirmed, CONFIRMED) == [
        'ok drag (300,126) → (60,130) through 2 points, confirmed ("delete")',
        'ok drag (201,400) → (362,722) through 2 points, confirmed ("trash")',
    ]


async def test_an_icon_only_button_is_known_by_its_identifier(tmp_path: Path) -> None:
    r = await groceries(tmp_path)
    said = await r.act([{"tap": "e7"}])
    assert said[0].startswith('error step 1: e7 "Clear" (56,542) looks like it changes something ("delete")')
    assert r.engine.hid_events == []


async def test_submit_or_return_is_refused_where_a_button_says_it_sends_unless_in_a_search_field(
    tmp_path: Path,
) -> None:
    r = await groceries(tmp_path)
    refused = (
        'presses return, which may press e6 "Send" on this screen, and that looks like it changes something ("send"); '
        "agents only observe here (agent.observe): it was not played."
    )
    said = await r.act([{"type": "Eggs", "into": "e5", "submit": True}])
    assert said[0].startswith(f"error step 1: submit {refused}")
    assert (await r.act([{"press": "return"}]))[0].startswith(f"error step 1: press return {refused}")
    assert r.engine.hid_events == []
    assert await r.act([{"type": "Eggs", "into": "e1", "submit": True}, {"type": "Eggs", "into": "e5"}]) == [
        'ok type "Eggs" into e1 "Search" (201,78) and submit',
        'ok type "Eggs" into e5 "Note" (146,622)',
    ]
    assert await r.act([{"type": "Eggs", "into": "e5", "submit": True}, {"press": "return"}], CONFIRMED) == [
        'ok type "Eggs" into e5 "Note" (146,622) and submit, confirmed ("send")',
        'ok press return, confirmed ("send")',
    ]


async def test_the_keyboards_delete_key_is_no_delete_and_return_with_nothing_to_send_is_played(tmp_path: Path) -> None:
    r = await on(tmp_path, fixture_json("ax-keyboard-ios27.json"))
    assert await r.act([{"tap": "e40"}, {"type": "Ann", "into": "e9", "submit": True}, {"press": "return"}]) == [
        'ok tap e40 "delete" (374,732)',
        'ok type "Ann" into e9 (201,415) and submit',
        "ok press return",
    ]


async def test_a_destructive_alert_button_is_refused_by_what_it_says(tmp_path: Path) -> None:
    # Recorded from the app SDK's sample: a UIAlertAction styled .destructive, which accessibility does not say.
    r = await on(tmp_path, fixture_json("ax-app-sdk-alert.json"))
    said = await r.act([{"tap": "e3"}])
    assert said[0].startswith('error step 1: e3 "Delete" (275,474) looks like it changes something ("delete")')
    assert (await r.act([{"tap": "e2"}]))[0] == 'ok tap e2 "Cancel" (127,474)'


async def test_one_its_app_styles_destructive_is_refused_whatever_it_says(tmp_path: Path) -> None:
    # The same alert as idb and the app SDK recorded it, its destructive "Delete" said "Yes": no word names it.
    def relabeled(text: str) -> Any:
        return json.loads(text.replace('"Delete"', '"Yes"'))

    r = await on(tmp_path, relabeled(json.dumps(fixture_json("ax-app-sdk-alert.json"))))
    shared = relabeled((APP_SDK_EXAMPLES / "hierarchy-alert.json").read_text())
    async with FakeAppSdk(shared) as app:
        shared["app"] |= {"bundle_id": app.bundle_id, "pid": app.pid}
        write_listing(Path(os.environ[DEVICES_DIR_ENV]) / r.instance.udid / "data", app.listing(r.instance.udid))
        extra = CombinedExtraReaders(AppHierarchyMerge())
        r.actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep, extra=extra)
        refused = (
            'e3 "Yes" (275,474) is a button its app styles destructive, and agents only observe here '
            "(agent.observe): it was not touched."
        )
        assert (await r.act([{"tap": "e3"}]))[0].startswith(f"error step 1: {refused}")
        assert (await r.act([{"tap": [275, 474]}]))[0].startswith(
            'error step 1: (275,474) on "Yes" is a button its app styles destructive'
        )
        assert r.engine.hid_events == []
        assert await r.act([{"tap": "e2"}, {"tap": "e3"}], CONFIRMED) == [
            'ok tap e2 "Cancel" (127,474)',
            'ok tap e3 "Yes" (275,474), confirmed ("destructive")',
        ]
        await r.actions.close()


#: Settings with a switch: on a real device, Wi-Fi off loses the device.
SWITCHES = {
    "elements": [
        framed("Application", "Settings", 0, 0, 402, 874, children=[
            framed("Switch", "Wi-Fi", 16, 120, 370, 44, value="1"),
            framed("Button", "About", 16, 180, 370, 44),
        ]),
    ]
}  # fmt: skip


async def test_a_switch_is_refused_by_ref_by_point_or_swiped_across_and_played_when_confirmed(tmp_path: Path) -> None:
    r = await on(tmp_path, SWITCHES)
    look = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert look.splitlines()[1:] == ['e1 switch "Wi-Fi" ="1" (201,142)', 'e2 button "About" (201,202)']
    assert await r.act([{"tap": "e1"}]) == [
        'error step 1: e1 "Wi-Fi" (201,142) is a switch, which changes a setting, and agents only observe here '
        "(agent.observe): it was not touched. If the person you work for asked for exactly this, call again with "
        "confirm: true"
    ]
    assert (await r.act([{"long_press": [300, 140]}]))[0].startswith(
        'error step 1: (300,140) on "Wi-Fi" is a switch, which changes a setting'
    )
    assert (await r.act([{"swipe": {"from": [300, 142], "direction": "left"}}]))[0].startswith(
        'error step 1: a sideways swipe from (300,142) on "Wi-Fi" can act on a switch, which changes a setting'
    )
    assert r.engine.hid_events == []
    assert await r.act([{"tap": "e2"}, {"swipe": {"from": "e1", "direction": "up"}}]) == [
        'ok tap e2 "About" (201,202)',
        'ok swipe e1 "Wi-Fi" (201,142) → (201,0)',
    ]
    assert (await r.act([{"tap": "e1"}], CONFIRMED)) == ['ok tap e1 "Wi-Fi" (201,142), confirmed ("switch")']


async def test_a_point_is_also_asked_of_the_device_which_names_what_no_snapshot_lists(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    # An icon that says nothing has no line, so the snapshot holds nothing at its point: the device's hit test does.
    r.engine.points[point_key(60, 480)] = framed("Button", "", 40, 460, 40, 40, identifier="trash")
    assert await r.act([{"tap": [60, 480]}]) == [
        'error step 1: (60,480) on "trash" looks like it changes something ("trash"), and agents only observe here '
        "(agent.observe): it was not touched. If the person you work for asked for exactly this, call again with "
        "confirm: true"
    ]
    assert r.engine.asked == [((60.0, 480.0),)]
    # What only holds the rest says nothing of what a tap does, whatever the app is called.
    r.engine.points[point_key(200, 600)] = framed("Application", "Delete Old Photos", 0, 0, 402, 874)
    assert await r.act([{"tap": [200, 600]}]) == ["ok tap (200,600)"]
    assert await r.act([{"tap": [60, 480]}], CONFIRMED) == ['ok tap (60,480), confirmed ("trash")']


async def test_a_device_that_cannot_say_what_is_at_a_point_leaves_the_snapshot_to_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await rigged(tmp_path)
    r.engine.points[point_key(330, 350)] = ConnectorError("the companion went away")
    r.engine.points[point_key(60, 480)] = ConnectorError("the companion went away")
    said = await r.act([{"tap": [60, 480]}, {"tap": [330, 350]}])
    assert said[0] == "ok tap (60,480)"
    assert said[1].startswith('error step 2: (330,350) on "Delete account" looks like it changes something')

    class Slow(FakeEngine):
        async def elements_at(self, points: Any) -> list[dict[str, Any] | None]:
            await asyncio.sleep(3600)
            raise AssertionError("never answers")

    monkeypatch.setattr("sim_mirror.perception.in_front.BUDGET_S", 0.01)
    folder = tmp_path / "slow"
    folder.mkdir()
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=Slow(document=ACCOUNT)))
    slow = Rigged(rig, await rig.up())
    await slow.actions.snapshot(slow.instance, CALLER, mode="full", max_elements=120)
    said = await slow.act([{"tap": [60, 480]}, {"tap": [330, 350]}])
    assert said[0] == "ok tap (60,480)"
    assert said[1].startswith('error step 2: (330,350) on "Delete account" looks like it changes something')


async def test_a_point_on_a_screen_read_by_no_hit_test_is_read_from_the_snapshot_alone(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    assert r.instance.session is not None
    r.instance.session.points = None
    assert (await r.act([{"tap": [330, 350]}]))[0].startswith('error step 1: (330,350) on "Delete account" looks')
    assert r.engine.asked == []
