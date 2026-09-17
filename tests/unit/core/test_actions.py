# SPDX-License-Identifier: Apache-2.0
"""An agent's steps on a device: each gesture announced then played, a person first, refs read against the screen, and
what the connector cannot do said plainly."""

from __future__ import annotations

import asyncio
import copy
import os
import re
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.merge import AppHierarchyMerge
from sim_mirror.connectors.base import Capability, ConnectorError, Crop, HidEvent, Shot
from sim_mirror.core import gestures
from sim_mirror.core.actions import MAX_STEPS, PERSON_WAIT_S, ActionError, AgentActions, check_steps
from sim_mirror.core.events import Event
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree
from sim_mirror.perception.ocr import OcrReaders, RecognizedLine
from sim_mirror.perception.readers import CombinedExtraReaders, TreeReader
from sim_mirror.platform.device_data import DEVICES_DIR_ENV
from sim_mirror.protocol import WORKING_EVERY_S
from sim_mirror.seams import Caller
from sim_mirror.testing.app_sdk import FakeAppSdk, app_hierarchy, app_node, write_listing
from sim_mirror.testing.fakes import JPEG, FakeConnector, FakeEngine, StaticConfig, fixture_json
from sim_mirror.testing.pictures import PictureEngine, picture
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope
from sim_mirror.testing.vision import FakeTextRecognizer, text_line

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code · checkout")
SETTINGS = fixture_json("ax-settings-interactable.json")


class Rigged:
    def __init__(self, rig: DeviceRig, instance: DeviceInstance) -> None:
        self.rig, self.instance = rig, instance
        self.slept: list[float] = []
        self.actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=self.sleep)
        self.events = instance.events.subscribe()

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.rig.clock.now += seconds
        await asyncio.sleep(0)

    @property
    def engine(self) -> FakeEngine:
        return self.rig.idb.engine

    def agent(self) -> list[Event]:
        published = [self.events.get_nowait() for _ in range(self.events.qsize())]
        return [event for event in published if event["type"] == "agent"]


async def rigged(folder: Path, engine: FakeEngine | None = None) -> Rigged:
    folder.mkdir(parents=True, exist_ok=True)
    rig = DeviceRig(folder, idb=FakeConnector("idb", engine=engine or FakeEngine()))
    return Rigged(rig, await rig.up())


def without(document: dict[str, Any], label: str) -> dict[str, Any]:
    changed = copy.deepcopy(document)
    children = changed["elements"][0]["children"]
    children[:] = [node for node in children if node.get("label") != label]
    return changed


def touches(events: list[HidEvent]) -> list[tuple[str, Any, str]]:
    """What the device was sent, with points rounded the way a line shows them."""
    return [(e.kind, e.code if e.kind == "key" else (round(e.x), round(e.y)), e.phase) for e in events]


# -- steps -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ("tap e2", f"steps must be a list of 1 to {MAX_STEPS} steps"),
        ([], "steps must be a list"),
        ([{"tap": "e1"}] * (MAX_STEPS + 1), "steps must be a list"),
        ([{"tap": "e1"}, {"hover": "e2"}], "step 2: a step is one of tap, long_press"),
        ([{"tap": "e1", "press": "home"}], "step 1: a step is one of"),
        (["tap"], "step 1: a step is one of"),
        (
            [{"type": "text", "into": "e1", "text": "Groceries"}],
            'step 1: type does not take "text"; beside it: "into", "clear", "submit" -- the words go in "type" itself',
        ),
        ([{"tap": "e1"}, {"tap": "e2", "ms": 900, "at": 1}], 'step 2: tap does not take "at", "ms"$'),
        ([{"type": "Trip", "clear": "yes"}], 'step 1: "clear" is true or false'),
        ([{"type": "Trip", "submit": 1}], 'step 1: "submit" is true or false'),
    ],
)
def test_malformed_steps_are_refused_before_anything_is_played(steps: Any, message: str) -> None:
    with pytest.raises(ActionError, match=message):
        check_steps(steps)


def test_well_formed_steps_say_what_they_are() -> None:
    typed = {"type": "Trip", "into": "e1", "clear": True, "submit": False}
    assert check_steps([{"tap": "e2"}, {"long_press": [1, 2], "ms": 900}, typed]) == [
        ("tap", "e2", {"tap": "e2"}),
        ("long_press", [1, 2], {"long_press": [1, 2], "ms": 900}),
        ("type", "Trip", typed),
    ]


# -- looking -----------------------------------------------------------------------------------------------------


async def test_a_snapshot_is_the_screen_the_first_time_and_what_changed_after(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    first = await r.actions.snapshot(r.instance, CALLER, mode="diff", max_elements=120)
    assert first.splitlines()[0].startswith("iOS 26.5 · Settings · 402x874pt · #")
    assert (await r.actions.snapshot(r.instance, CALLER, mode="diff", max_elements=120)).startswith("no change · #")
    r.engine.document = without(SETTINGS, "Camera")
    changed = await r.actions.snapshot(r.instance, CALLER, mode="diff", max_elements=120)
    assert '- e6 button "Camera" (201,527)' in changed
    assert (await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)).startswith("iOS 26.5")
    looks = r.agent()
    assert [(e["phase"], e.get("gesture", {}).get("kind"), e.get("ok")) for e in looks[:2]] == [
        ("intent", "look", None),
        ("done", None, True),
    ]
    assert looks[0]["agent"] == {"key": CALLER.key, "title": CALLER.title}


class Extra:
    """Extra readers a test sets: one tree for every device, and what the actions asked of them."""

    def __init__(self, tree: ScreenTree) -> None:
        self.tree = tree
        self.asked: list[tuple[str, str, bool]] = []
        self.forgotten: list[str] = []
        self.closed = False

    def readers(self, udid: str, connector: str, config: SimConfig) -> tuple[TreeReader, ...]:
        self.asked.append((udid, connector, config.mcpbridge_merge))
        return (self,)

    async def read(self) -> ScreenTree:
        return self.tree

    def forget(self, udid: str) -> None:
        self.forgotten.append(udid)

    async def close(self) -> None:
        self.closed = True


async def test_a_snapshot_merges_what_the_scopes_extra_readers_find_and_lets_them_go_with_the_device(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path)
    web = ElementNode(role="Link", label="Learn more", frame=Frame(80, 316, 83, 21), source="mcpbridge")
    extra = Extra(ScreenTree(roots=(web,), notes=("Xcode read it",)))
    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep, extra=extra)
    r.rig.config.set(mcpbridge_merge=True)
    text = await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert 'link "Learn more" (122,326)' in text and text.endswith("Xcode read it")
    assert extra.asked == [(r.instance.udid, "idb", True)]
    await r.rig.manager.stop(CALLER.scope)
    assert extra.forgotten == [r.instance.udid]
    await actions.close()
    assert extra.closed


async def test_an_app_sharing_its_hierarchy_names_and_adds_to_a_snapshot_and_screens_hear_which_app(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path)
    document = app_hierarchy(
        app_node("search", "Search settings", (33, 803, 336, 38), interactive=True),
        app_node("button", "Share card", (16, 712, 120, 28)),
        app_node("text", "General", (30, 305, 100, 28)),
    )
    events = r.instance.events.subscribe()
    async with FakeAppSdk(document) as app:
        write_listing(Path(os.environ[DEVICES_DIR_ENV]) / r.instance.udid / "data", app.listing(r.instance.udid))
        extra = CombinedExtraReaders(AppHierarchyMerge(on_share=r.rig.manager.share_app))
        actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep, extra=extra)
        text = await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert 'search "Search settings" ="Search"' in text and 'button "Share card" (76,726)' in text
    assert 'search ="Search"' not in text and text.count('"General"') == 1
    shared = {"name": "AppSDK", "bundle_id": app.bundle_id, "sdk_version": "1.0.0"}
    assert r.instance.app_hierarchy == shared
    assert [event["app_hierarchy"] for event in drain(events) if event.get("type") == "status"] == [shared]
    r.rig.config.set(app_merge=False)
    assert "Share card" not in await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert r.instance.app_hierarchy is None


def drain(events: asyncio.Queue[Event]) -> list[Event]:
    found = []
    while not events.empty():
        found.append(events.get_nowait())
    return found


async def test_a_screen_that_cannot_be_read_says_to_use_a_screenshot(tmp_path: Path) -> None:
    engine = FakeEngine()
    engine.accessibility_errors = [ConnectorError("reading the screen failed: no tree")]
    r = await rigged(tmp_path, engine)
    with pytest.raises(ActionError, match="no tree; sim_screenshot still shows the screen"):
        await r.actions.snapshot(r.instance, CALLER, mode="diff", max_elements=120)
    assert r.agent()[-1] == {"type": "agent", "id": "a1", "phase": "done", "ok": False}


async def test_screenshots_are_taken_once_or_as_frames_of_an_element_or_a_region(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    shots = await r.actions.screenshots(r.instance, CALLER, width=400, frames=3, interval_ms=100)
    assert shots == [Shot(JPEG, 402, 874)] * 3 and r.slept == [0.1, 0.1]
    assert r.engine.screenshots[-1] == (400, 70, None)
    await r.actions.screenshots(r.instance, CALLER, width=800, region="e2")
    width, quality, crop = r.engine.screenshots[-1]
    assert (width, quality) == (800, 70) and crop is not None
    assert (round(crop.x), round(crop.y), crop.width, crop.height) == (141, 259, 120.0, 120.0)
    await r.actions.screenshots(r.instance, CALLER, width=800, region={"x": 0, "y": 0, "w": 100, "h": 50})
    assert r.engine.screenshots[-1][2] == Crop(0.0, 0.0, 100.0, 50.0)
    assert [e["phase"] for e in r.agent()][-2:] == ["intent", "done"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"width": 5000}, "width must be a whole number of pixels from 160 to 1200"),
        ({"width": 400, "frames": 0}, "frames must be a whole number from 1 to 6"),
        ({"width": 400, "frames": True}, "frames must be"),
        ({"width": 400, "interval_ms": 10}, "interval_ms must be a whole number of milliseconds from 50 to 2000"),
        ({"width": 400, "region": {"x": 0, "y": 0, "w": 0, "h": 5}}, "region is a ref"),
        ({"width": 400, "region": "e99"}, "e99 is not on screen now"),
    ],
)
async def test_a_screenshot_asked_for_wrongly_says_how_to_ask(
    tmp_path: Path, kwargs: dict[str, Any], message: str
) -> None:
    r = await rigged(tmp_path)
    with pytest.raises(ActionError, match=message):
        await r.actions.screenshots(r.instance, CALLER, **kwargs)


async def test_a_screenshot_the_device_refuses_is_an_error(tmp_path: Path) -> None:
    engine = FakeEngine()
    engine.screenshot_errors = [ConnectorError("taking a screenshot failed: gone")]
    r = await rigged(tmp_path, engine)
    with pytest.raises(ActionError, match="taking a screenshot failed: gone"):
        await r.actions.screenshots(r.instance, CALLER, width=400)
    assert r.agent()[-1]["ok"] is False


# -- touching ----------------------------------------------------------------------------------------------------


async def test_a_tap_by_ref_is_announced_then_played_where_the_element_is(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")
    assert answer == 'ok tap e2 "General" (201,319)'
    assert touches(r.engine.hid_events) == [("touch", (201, 319), "down"), ("touch", (201, 319), "up")]
    intent, done = r.agent()
    assert intent["phase"] == "intent"
    assert intent["gesture"] == {"kind": "tap", "duration_ms": 50, "points": [(0.5, 0.3654)]}
    assert (intent["label"], intent["lead_ms"], intent["pointer"], intent["linger_ms"]) == (
        "General",
        250,
        True,
        60_000,
    )
    assert done == {"type": "agent", "id": intent["id"], "phase": "done", "ok": True}
    assert r.slept == [0.05]


async def test_the_pointer_gets_a_head_start_only_while_someone_watches_and_the_cursor_is_on(tmp_path: Path) -> None:
    r = await rigged(tmp_path)

    async def close(code: int, reason: str) -> None:
        return None

    r.rig.manager.attach(r.instance, close)
    await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")
    assert r.slept == [0.25, 0.05]
    r.slept.clear()
    await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none", cursor=False)
    assert r.slept == [0.05]


async def test_with_the_agent_cursor_off_screens_hear_an_agent_but_get_nothing_to_draw(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.rig.config.set(agent_cursor=False)
    await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none", cursor=False)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    intents = [event for event in r.agent() if event["phase"] == "intent"]
    drawn = [(e["pointer"], e["gesture"]["kind"], e["gesture"]["points"], e["label"], e["caption"], e["lead_ms"])
             for e in intents]  # fmt: skip
    assert drawn == [(False, "tap", [], "", "", 0), (False, "look", [], "", "", 0)]
    assert intents[0]["agent"] == {"key": CALLER.key, "title": CALLER.title}
    assert [event["linger_ms"] for event in intents] == [60_000, 60_000]
    assert touches(r.engine.hid_events) == [("touch", (201, 319), "down"), ("touch", (201, 319), "up")]


async def test_an_agent_at_work_is_told_to_the_screens_when_a_call_starts_while_it_runs_and_when_it_ends(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path)
    ticks: list[float] = []
    release = asyncio.Event()

    async def tick(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) > 2:  # two beats while a long call runs, then it stays running
            await release.wait()

    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep, tick=tick)
    r.rig.config.set(cursor_linger_s=90)
    async with actions.working(CALLER):
        for _ in range(5):
            await asyncio.sleep(0)
        r.rig.config.set(cursor_linger_s=0)
    working = r.agent()
    assert [(e["phase"], e["agent"]["title"], e["ongoing"], e["linger_ms"]) for e in working] == [
        ("working", CALLER.title, True, 90_000),
        ("working", CALLER.title, True, 90_000),
        ("working", CALLER.title, True, 90_000),
        ("working", CALLER.title, False, 0),
    ]
    assert len({event["id"] for event in working}) == 4 and ticks == [WORKING_EVERY_S] * 3
    assert not release.is_set()  # the beat was cancelled, not let run out

    elsewhere = Caller(scope("tp-9"), key="agent-2", title="Codex")
    async with actions.working(elsewhere):
        pass
    assert r.agent() == []  # no device for that scope: nobody to tell


async def test_every_kind_of_step_says_what_it_did(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    steps = [
        {"tap": [10, 20]},
        {"long_press": "e2"},
        {"long_press": [5, 5], "ms": 1200},
        {"swipe": {"from": "e2", "direction": "up"}},
        {"swipe": {"from": [201, 100], "direction": "up", "ms": 200}},
        {"swipe": {"from": [10, 10], "to": [300, 10]}},
        {"drag": {"path": [[10, 10], [20, 20], [30, 30]]}},
        {"type": "Trip", "into": "e12", "submit": True},
        {"type": "no field"},
        {"press": "home"},
        {"press": "return"},
        {"pause": 500},
    ]
    answer = await r.actions.act(r.instance, CALLER, steps, snapshot="none")
    assert answer.splitlines() == [
        "ok tap (10,20)",
        'ok long press e2 "General" (201,319) 800ms',
        "ok long press (5,5) 1200ms",
        'ok swipe e2 "General" (201,319) → (201,13)',
        "ok swipe (201,100) → (201,0)",
        "ok swipe (10,10) → (300,10)",
        "ok drag (10,10) → (30,30) through 3 points",
        'ok type "Trip" into e12 (201,822) and submit',
        'ok type "no field"',
        "ok press home",
        "ok press return",
        "ok pause 500ms",
    ]
    pasted = [call for call in r.rig.xcrun.calls if call.args[1] == "pbcopy"]
    assert [call.input_data for call in pasted] == [b"Trip", b"no field"]
    kinds = [event["gesture"]["kind"] for event in r.agent() if event["phase"] == "intent"]
    assert kinds == ["tap", "long_press", "long_press", "swipe", "swipe", "swipe", "drag", "type", "type", "press",
                     "press"]  # fmt: skip
    assert 0.5 in r.slept


async def test_typing_into_a_field_taps_it_pastes_and_submits(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    await r.actions.act(r.instance, CALLER, [{"type": "Trip", "into": "e12", "submit": True}], snapshot="none")
    assert touches(r.engine.hid_events) == [
        ("touch", (201, 822), "down"),
        ("touch", (201, 822), "up"),
        ("key", gestures.COMMAND_KEY, "down"),
        ("key", gestures.V_KEY, "down"),
        ("key", gestures.V_KEY, "up"),
        ("key", gestures.COMMAND_KEY, "up"),
        ("key", 40, "down"),
        ("key", 40, "up"),
    ]
    intent = next(event for event in r.agent() if event["phase"] == "intent")
    assert intent["caption"] == "Trip" and intent["gesture"]["points"] == [(0.5, 0.9405)]


async def test_typing_where_the_text_has_keys_types_it_and_submits_once_the_last_key_is_up(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.rig.keyboard.us = True
    answer = await r.actions.act(r.instance, CALLER, [{"type": "Ok", "into": "e12", "submit": True}], snapshot="none")
    assert answer == 'ok type "Ok" into e12 (201,822) and submit'
    assert touches(r.engine.hid_events) == [
        ("touch", (201, 822), "down"),
        ("touch", (201, 822), "up"),
        ("key", gestures.SHIFT_KEY, "down"),
        ("key", 18, "down"),
        ("key", 18, "up"),
        ("key", gestures.SHIFT_KEY, "up"),
        ("key", 14, "down"),
        ("key", 14, "up"),
        ("key", 40, "down"),
        ("key", 40, "up"),
    ]
    assert not any(call.args[1] == "pbcopy" for call in r.rig.xcrun.calls) and r.rig.keyboard.asked == 1


async def test_a_paste_to_ios_27_says_it_may_have_been_refused_and_one_to_ios_26_does_not(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.instance.runtime = "iOS 27.0"
    answer = await r.actions.act(r.instance, CALLER, [{"type": "Київ"}, {"type": "wifi"}], snapshot="none")
    refused = ", and iOS 27.0 refuses a paste without asking: check the field"
    assert answer.splitlines() == [
        f"ok type \"Київ\" -- pasted, since 'К' has no key to type it with{refused}",
        f'ok type "wifi" -- pasted, since the Mac\'s keyboard layout is not US or ABC{refused}',
    ]
    r.rig.keyboard.us = True
    assert await r.actions.act(r.instance, CALLER, [{"type": "wifi"}], snapshot="none") == 'ok type "wifi"'
    r.instance.runtime = "iOS 26.5"
    assert await r.actions.act(r.instance, CALLER, [{"type": "Київ"}], snapshot="none") == 'ok type "Київ"'


async def test_typing_with_clear_selects_what_the_field_holds_and_deletes_it_before_pasting(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    steps = [{"type": "Trip", "into": "e12", "clear": True, "submit": True}]
    answer = await r.actions.act(r.instance, CALLER, steps, snapshot="none")
    assert answer.splitlines() == ['ok type "Trip" into e12 (201,822), replacing what it held and submit']
    assert touches(r.engine.hid_events) == [
        ("touch", (201, 822), "down"),
        ("touch", (201, 822), "up"),
        ("key", gestures.COMMAND_KEY, "down"),
        ("key", gestures.A_KEY, "down"),
        ("key", gestures.A_KEY, "up"),
        ("key", gestures.COMMAND_KEY, "up"),
        ("key", 42, "down"),
        ("key", 42, "up"),
        ("key", gestures.COMMAND_KEY, "down"),
        ("key", gestures.V_KEY, "down"),
        ("key", gestures.V_KEY, "up"),
        ("key", gestures.COMMAND_KEY, "up"),
        ("key", 40, "down"),
        ("key", 40, "up"),
    ]
    assert [call.input_data for call in r.rig.xcrun.calls if call.args[1] == "pbcopy"] == [b"Trip"]


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"tap": "e99"}, r"e99 is not on screen now \(#[0-9a-f]{4}\); call sim_snapshot"),
        ({"tap": [500, 10]}, r"\(500,10\) is off the 402x874pt screen"),
        ({"tap": {"x": 1}}, "a target is a ref"),
        ({"long_press": "e2", "ms": 10}, "ms must be a whole number of milliseconds from 50 to 5000"),
        ({"swipe": "up"}, "a swipe is"),
        ({"swipe": {"from": "e2"}}, 'a swipe needs "to" or a "direction"'),
        ({"drag": {"path": [[1, 1]]}}, "a drag is"),
        ({"drag": {"path": [[1, 1], "e99"]}}, "e99 is not on screen now"),
        ({"type": ""}, "type takes text of 1 to 2000 characters"),
        ({"press": "power"}, "press takes one of"),
        ({"pause": 9000}, "pause must be a whole number of milliseconds from 0 to 3000"),
    ],
)
async def test_a_step_that_cannot_be_played_ends_the_batch_and_says_why(
    tmp_path: Path, step: dict[str, Any], message: str
) -> None:
    r = await rigged(tmp_path)
    answer = (
        await r.actions.act(r.instance, CALLER, [{"tap": "e2"}, step, {"tap": "e3"}], snapshot="none")
    ).splitlines()
    assert answer[0] == 'ok tap e2 "General" (201,319)' and len(answer) == 2
    assert answer[1].startswith("error step 2: ") and re.search(message, answer[1])
    assert len(r.engine.hid_events) == 2


async def test_after_the_steps_the_answer_shows_what_the_screen_became(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    r.engine.document = without(SETTINGS, "Camera")
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e2"}])
    assert answer.splitlines()[0] == 'ok tap e2 "General" (201,319)' and '- e6 button "Camera"' in answer
    full = await r.actions.act(r.instance, CALLER, [{"pause": 0}], snapshot="full")
    assert full.splitlines()[1].startswith("iOS 26.5 · Settings")


async def test_a_device_that_is_busy_or_in_a_persons_hands_refuses(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.instance.busy = "tests running"
    with pytest.raises(ActionError, match="the device is busy: tests running"):
        await r.actions.act(r.instance, CALLER, [{"tap": "e2"}])
    r.instance.busy = None

    async def still_touching(seconds: float) -> None:
        r.rig.clock.now += seconds
        r.instance.person_touch_at = r.rig.clock.now
        await asyncio.sleep(0)

    r.instance.person_touch_at = r.rig.clock.now
    r.actions._sleep = still_touching
    with pytest.raises(ActionError, match="the person watching is using the device"):
        await r.actions.act(r.instance, CALLER, [{"tap": "e2"}])
    assert r.rig.clock.now >= 100 + PERSON_WAIT_S
    r.actions._sleep = r.sleep
    r.instance.person_touch_at = r.rig.clock.now
    assert (await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")).startswith("ok tap")
    assert r.slept[:6] == [0.05] * 6


async def test_a_device_still_booting_is_not_ready_for_agents(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", hold=True))
    instance = await rig.manager.ensure(scope("tp-1"))
    actions = AgentActions(rig.manager, rig.config)
    with pytest.raises(ActionError, match=r"not ready yet \(it is booting\)"):
        await actions.act(instance, CALLER, [{"tap": "e2"}])
    with pytest.raises(ActionError, match="not ready yet"):
        await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    await rig.manager.shutdown()


async def test_input_or_a_pasteboard_the_device_refuses_is_an_error_line(tmp_path: Path) -> None:
    class Refusing(FakeEngine):
        async def hid(self, events: AsyncIterable[HidEvent]) -> None:
            raise ConnectorError("sending input failed: companion gone")

    r = await rigged(tmp_path / "refusing", Refusing())
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")
    assert answer == 'error step 1: tap e2 "General" (201,319) failed: sending input failed: companion gone'
    assert r.agent()[-1]["ok"] is False
    r = await rigged(tmp_path / "pasteboard")
    r.rig.xcrun.on("simctl", "pbcopy", rc=1, err="Invalid device")
    answer = await r.actions.act(r.instance, CALLER, [{"type": "hi"}], snapshot="none")
    assert answer == 'error step 1: type "hi" failed: simctl pbcopy: Invalid device'


async def test_a_device_that_went_away_mid_batch_ends_it(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)

    async def gone(seconds: float) -> None:
        r.instance.session = None
        await asyncio.sleep(0)

    r.actions._sleep = gone
    answer = await r.actions.act(r.instance, CALLER, [{"pause": 10}, {"tap": "e2"}])
    assert answer.splitlines() == [
        "ok pause 10ms",
        "error step 2: the simulator is not ready yet (it is ready); try again in a moment",
    ]


async def test_a_mirror_that_cannot_read_or_touch_the_screen_says_which_connector_could(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False), simctl=FakeConnector("simctl",
                    capabilities=VIEW_ONLY))  # fmt: skip
    instance = await rig.up()
    actions = AgentActions(rig.manager, rig.config)
    with pytest.raises(ActionError, match="reading the screen needs a connector with an element tree, such as idb"):
        await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    with pytest.raises(ActionError, match="shown through simctl, which cannot touch it"):
        await actions.act(instance, CALLER, [{"tap": [10, 10]}])
    shots = await actions.screenshots(instance, CALLER, width=400)
    assert len(shots) == 1


# -- waiting -----------------------------------------------------------------------------------------------------


async def test_waiting_for_text_that_is_there_comes_back_at_once_and_for_text_that_comes(tmp_path: Path) -> None:
    with_done = copy.deepcopy(SETTINGS)
    with_done["elements"][0]["children"].append(
        {"type": "Button", "label": "Done", "frame": {"x": 300, "y": 60, "width": 80, "height": 40}}
    )

    class Arriving(FakeEngine):
        reads = 0

        async def accessibility(self) -> dict[str, Any]:
            self.reads += 1
            return with_done if self.reads >= 3 else SETTINGS

    r = await rigged(tmp_path, Arriving())
    wait = {"for": "Done", "timeout_ms": 1000}
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], wait=wait, snapshot="none")
    assert answer.splitlines()[1] == 'waited 150ms for "Done"'
    again = await r.actions.act(r.instance, CALLER, [{"pause": 0}], wait={"for": "done"}, snapshot="none")
    assert again.splitlines()[1] == 'waited 0ms for "done"'


@pytest.mark.parametrize(
    ("wait", "line"),
    [
        ({"for": "a", "gone": "b"}, 'wait skipped: give one of {"for": text}, {"gone": text} or {"settle_ms": ms}'),
        ("Done", "wait skipped: give one of"),
        ({"for": ""}, "wait skipped: wait takes text to look for"),
        ({"settle_ms": 1}, "wait skipped: settle_ms must be a whole number of milliseconds from 100 to 3000"),
    ],
)
async def test_a_wait_asked_for_wrongly_is_skipped_with_why(tmp_path: Path, wait: Any, line: str) -> None:
    r = await rigged(tmp_path)
    answer = await r.actions.act(r.instance, CALLER, [{"pause": 0}], wait=wait, snapshot="none")
    assert answer.splitlines()[1].startswith(line)


async def test_steps_already_played_are_still_said_when_the_screen_cannot_be_read_after_them(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    r.engine.accessibility_errors = [ConnectorError("reading the screen failed: kAXErrorServerNotFound")] * 5
    answer = await r.actions.act(r.instance, CALLER, [{"tap": [10, 20]}], wait={"for": "General"}, snapshot="diff")
    lines = answer.splitlines()
    assert lines[0] == "ok tap (10,20)" and len(lines) == 3
    assert lines[1].startswith("wait stopped: reading the screen failed: kAXErrorServerNotFound")
    assert lines[2].startswith("the screen after the steps could not be read: reading the screen failed")
    assert touches(r.engine.hid_events) == [("touch", (10, 20), "down"), ("touch", (10, 20), "up")]


async def test_no_wait_after_a_step_that_failed(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    answer = await r.actions.act(r.instance, CALLER, [{"tap": "e99"}], wait={"for": "General"}, snapshot="none")
    assert len(answer.splitlines()) == 1 and answer.startswith("error step 1")


def animating(moving: int) -> PictureEngine:
    """A screen whose box moves for its first `moving` screenshots, then keeps still."""
    return PictureEngine(lambda look: picture(boxes=[(0, 5 * min(look, moving), 80, 40, 0)]))


@pytest.mark.parametrize(
    ("moving", "wait", "line"),
    [
        (0, {"settle_ms": 300}, "settled after 0ms"),
        (2, {"settle_ms": 300}, "settled after 300ms"),
        (99, {"settle_ms": 300, "timeout_ms": 450}, "still changing after 450ms"),
    ],
)
async def test_waiting_for_an_animation_to_settle(tmp_path: Path, moving: int, wait: dict[str, int], line: str) -> None:
    r = await rigged(tmp_path, animating(moving))
    answer = await r.actions.act(r.instance, CALLER, [{"pause": 0}], wait=wait, snapshot="none")
    assert answer.splitlines()[1] == line


@pytest.mark.parametrize(
    ("mode", "line"),
    [("perceptual", "settled after 450ms (18 small places kept moving and were not watched)"),
     ("exact", "still changing after 1500ms")],
)  # fmt: skip
async def test_a_spinner_settles_as_the_scope_says_to_watch_it(tmp_path: Path, mode: str, line: str) -> None:
    r = await rigged(tmp_path, PictureEngine(lambda look: picture(boxes=[(60 + 10 * (look % 3), 170, 10, 10, 0)])))
    r.rig.config.set(settle_mode=mode)
    answer = await r.actions.act(r.instance, CALLER, [{"pause": 0}], wait={"settle_ms": 300, "timeout_ms": 1500})
    assert answer.splitlines()[1] == line


async def test_work_an_agent_does_off_the_screen_is_shown_under_a_caption(tmp_path: Path) -> None:
    r = await rigged(tmp_path)

    async def launch() -> int:
        return 81234

    assert await r.actions.announced(r.instance, CALLER, "app", "launch com.acme.Notes", launch()) == 81234
    intent, done = r.agent()
    assert intent["gesture"] == {"kind": "app", "duration_ms": 0, "points": []}
    assert intent["caption"] == "launch com.acme.Notes" and done["ok"] is True

    async def broken() -> None:
        raise ConnectorError("the build failed")

    with pytest.raises(ConnectorError, match="the build failed"):
        await r.actions.announced(r.instance, CALLER, "build", "x" * 200, broken())
    intent, done = r.agent()
    assert len(intent["caption"]) == 80 and done["ok"] is False


# -- reading pixels ----------------------------------------------------------------------------------------------

SIGN_IN = text_line("Sign in", 150, 400, 100, 20)
WELCOME = text_line("Welcome back", 100, 200, 200, 30)


def reading(r: Rigged, *lines: RecognizedLine) -> tuple[AgentActions, FakeTextRecognizer]:
    recognizer = FakeTextRecognizer(lines)
    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep,
                           pixels=OcrReaders(recognizer, supported=True))  # fmt: skip
    return actions, recognizer


async def test_a_mirror_that_cannot_read_a_tree_reads_the_screens_pixels_while_its_scope_does(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False), simctl=FakeConnector("simctl",
                    capabilities=VIEW_ONLY))  # fmt: skip
    instance = await rig.up()
    recognizer = FakeTextRecognizer([SIGN_IN])
    actions = AgentActions(rig.manager, rig.config, pixels=OcrReaders(recognizer, supported=True))
    assert Capability.ELEMENT_TREE in actions.capabilities(instance.capabilities, rig.config.get(CALLER.scope))
    text = await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    assert text.splitlines()[1:] == [
        'e1 text "Sign in" (200,410)',
        "1 line was read from the screen's pixels: text may be misread, and a ref taps its middle",
    ]
    rig.config.set(ocr_mode="off")
    assert actions.capabilities(instance.capabilities, rig.config.get(CALLER.scope)) == instance.capabilities
    with pytest.raises(ActionError, match=r"or perception\.ocr on; this device is shown through simctl"):
        await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    await rig.manager.stop(CALLER.scope)
    await actions.close()
    assert recognizer.closed


def test_pixels_add_nothing_to_what_a_connector_already_reads_or_cannot_show(tmp_path: Path) -> None:
    config = SimConfig.defaults()
    actions = AgentActions(DeviceRig(tmp_path).manager, StaticConfig(),
                           pixels=OcrReaders(FakeTextRecognizer(), supported=True))  # fmt: skip
    full = frozenset({Capability.ELEMENT_TREE, Capability.SCREENSHOT})
    assert actions.capabilities(full, config) == full
    assert actions.capabilities({Capability.LOGS}, config) == frozenset({Capability.LOGS})
    unsupported = AgentActions(DeviceRig(tmp_path).manager, StaticConfig())
    assert unsupported.capabilities({Capability.SCREENSHOT}, config) == frozenset({Capability.SCREENSHOT})


async def test_a_tree_that_says_nothing_falls_back_to_pixels_and_one_that_says_anything_does_not(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path)
    actions, recognizer = reading(r, SIGN_IN)
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert recognizer.readings == []
    r.engine.document = {"elements": [{"type": "Application", "label": "Game"}]}
    lines = (await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)).splitlines()
    # Refs go on from the snapshot before: the Settings screen took the first thirteen.
    assert lines[1] == 'e14 text "Sign in" (200,410)'
    assert lines[2].startswith("accessibility said nothing on this screen")
    assert len(recognizer.readings) == 1


async def test_merging_pixels_adds_the_text_the_tree_leaves_out(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    # "General" is read inside the General button (201,319): said already, so not added again.
    actions, recognizer = reading(r, text_line("General", 30, 305, 100, 28), WELCOME)
    r.rig.config.set(ocr_mode="merge")
    text = await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert 'text "Welcome back" (200,215)' in text and text.count('"General"') == 1
    assert len(recognizer.readings) == 1


async def test_an_agent_taps_and_waits_for_text_read_from_pixels(tmp_path: Path) -> None:
    engine = FakeEngine(document={"elements": []})
    r = await rigged(tmp_path, engine)
    actions, recognizer = reading(r, SIGN_IN)
    answer = await actions.act(r.instance, CALLER, [{"tap": "e1"}], wait={"for": "sign in"}, snapshot="none")
    assert answer.splitlines()[1] == 'waited 0ms for "sign in"'
    assert touches(engine.hid_events) == [("touch", (200, 410), "down"), ("touch", (200, 410), "up")]
    recognizer.lines = (WELCOME,)
    engine.shot = Shot(b"welcome", 402, 874)
    answer = await actions.act(r.instance, CALLER, [{"pause": 0}], wait={"gone": "Sign in"}, snapshot="diff")
    assert answer.splitlines()[1] == 'waited 0ms for "Sign in" to go'
    assert 'e2 text "Welcome back" (200,215)' in answer.splitlines()


async def test_viewers_outline_what_was_read_from_pixels_until_the_screen_is_about_to_change(tmp_path: Path) -> None:
    engine = FakeEngine(document={"elements": []})
    r = await rigged(tmp_path, engine)
    actions, _ = reading(r, SIGN_IN)
    r.rig.config.set(ocr_overlay=True)

    def told() -> list[tuple[str, Any]]:
        published = [r.events.get_nowait() for _ in range(r.events.qsize())]
        return [(event["type"], event.get("phase") or [box["text"] for box in event.get("boxes", ())])
                for event in published]  # fmt: skip

    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=10)
    assert told() == [("agent", "intent"), ("screen_text", ["Sign in"]), ("agent", "done")]
    await actions.act(r.instance, CALLER, [{"pause": 0}], snapshot="none")
    assert ("screen_text", []) not in told()
    await actions.act(r.instance, CALLER, [{"tap": "e1"}], snapshot="none")
    assert told()[:2] == [("screen_text", []), ("agent", "intent")]
    engine.document = SETTINGS
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=10)
    engine.document = {"elements": []}
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=10)
    r.rig.config.set(ocr_overlay=False)
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=10)
    assert [event for event in told() if event[0] == "screen_text"] == [
        ("screen_text", ["Sign in"]),
        ("screen_text", []),
    ]
    assert not r.instance.text.shown
