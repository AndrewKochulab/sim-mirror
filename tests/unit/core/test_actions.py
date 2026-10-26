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
from sim_mirror.connectors.base import Capability, ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.core import gestures
from sim_mirror.core.actions import MAX_STEPS, PAUSED, PERSON_WAIT_S, ActionError, AgentActions, check_steps
from sim_mirror.core.events import Event
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree
from sim_mirror.perception.ocr import OcrReaders, RecognizedLine, TextRecognitionError
from sim_mirror.perception.readers import CombinedExtraReaders, TreeReader
from sim_mirror.platform.device_data import DEVICES_DIR_ENV
from sim_mirror.protocol import WORKING_EVERY_S
from sim_mirror.seams import Caller
from sim_mirror.testing.app_sdk import FakeAppSdk, app_hierarchy, app_node, write_listing
from sim_mirror.testing.fakes import JPEG, FakeConnector, FakeEngine, FakeInputPause, StaticConfig, fixture_json
from sim_mirror.testing.pictures import PictureEngine, picture
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope
from sim_mirror.testing.vision import FakeTextRecognizer, text_line
from sim_mirror.tools.device import device_line

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
        # A drag's hold_ms was dropped and the drag played without it.
        (
            [{"drag": {"path": [[1, 2], [3, 4]], "hold_ms": 500}}],
            'step 1: a drag does not take "hold_ms"; it takes "path", "ms" -- a drag moves as soon as it touches',
        ),
        (
            [{"tap": "e1"}, {"swipe": {"from": "e1", "direction": "up", "speed": 2, "at": 1}}],
            'step 2: a swipe does not take "at", "speed"; it takes "from", "to", "direction", "ms"$',
        ),
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


class Changing(FakeEngine):
    """An app whose screen changes as soon as it is touched -- a keyboard opening over it, say."""

    def __init__(self, after: dict[str, Any]) -> None:
        super().__init__()
        self.after = after

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        await super().hid(events)
        self.document = self.after


async def test_a_ref_after_a_step_that_changed_the_screen_is_read_again_and_refused_where_it_went(
    tmp_path: Path,
) -> None:
    engine = Changing(without(SETTINGS, "Camera"))
    r = await rigged(tmp_path, engine)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    said = await r.actions.act(r.instance, CALLER, [{"tap": "e5"}, {"tap": "e6"}], max_elements=120)
    assert said.splitlines()[0].startswith("ok tap e5")
    assert said.splitlines()[1].startswith("error step 2: e6 is not on screen now"), said
    assert len(touches(engine.hid_events)) == 2, "only the first tap went to the device"
    engine.after = engine.document
    paused = await r.actions.act(r.instance, CALLER, [{"pause": 10}, {"tap": "e5"}], max_elements=120)
    assert "ok tap e5" in paused, "a pause changes nothing, so the snapshot stands"


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
    r.instance.kind = "physical"
    mine = await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert "Learn more" not in mine and len(extra.asked) == 1, "a real device has no simulator folders to merge from"
    r.instance.kind = "simulator"
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
        ({"press": "back"}, "this device has no back button to press; it has home, lock, side, siri"),
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


def framed(kind: str, label: str, x: float, y: float, width: float, height: float, **more: Any) -> dict[str, Any]:
    return {"type": kind, "label": label, "frame": {"x": x, "y": y, "width": width, "height": height}, **more}


#: A grid scrolled on under a tab bar, read as idb_companion reads one: the bar a group with the tab bar's trait.
UNDER_TAB_BAR = {
    "elements": [
        framed("Application", "Grid", 0, 0, 402, 874, children=[
            framed("Button", "Item 7", 16, 740, 180, 80),
            framed("Button", "Item 9", 16, 820, 180, 46),
            framed("TextField", "Note", 206, 820, 180, 46),
            framed("Group", "Tab Bar", 0, 790, 402, 84, traits=["TabBar"], children=[
                framed("Button", "Grid", 154, 795, 94, 54),
            ]),
        ]),
    ]
}  # fmt: skip


async def test_a_ref_under_a_tab_bar_is_refused_with_how_to_reach_it_and_nothing_lands_on_the_bar(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path, FakeEngine(document=UNDER_TAB_BAR))
    look = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    assert look.splitlines()[1:] == [
        'e1 button "Item 7" (106,765)',
        'e2 button "Item 9" (106,843) covered by tab bar',
        'e3 field "Note" (296,843) covered by tab bar',
        'e4 button "Grid" (201,822)',
    ]
    said = (await r.actions.act(r.instance, CALLER, [{"tap": "e1"}, {"tap": "e2"}], snapshot="none")).splitlines()
    assert said == [
        'ok tap e1 "Item 7" (106,765)',
        'error step 2: e2 "Item 9" is under the tab bar: a touch at (106,843) would land on the tab bar. First scroll '
        "it into view with a swipe that starts on the content, not the bar, and then use e2 again",
    ]
    assert touches(r.engine.hid_events) == [("touch", (106, 765), "down"), ("touch", (106, 765), "up")]


@pytest.mark.parametrize(
    ("step", "played"),
    [
        ({"tap": "e4"}, True),
        ({"swipe": {"from": "e1", "to": "e2"}}, True),
        ({"drag": {"path": ["e1", "e2"]}}, True),
        ({"swipe": {"from": "e2", "direction": "up"}}, False),
        ({"long_press": "e2"}, False),
        ({"drag": {"path": ["e2", "e1"]}}, False),
        ({"type": "Hi", "into": "e3"}, False),
    ],
)
async def test_only_a_touch_that_would_begin_under_the_bar_is_refused(
    tmp_path: Path, step: dict[str, Any], played: bool
) -> None:
    r = await rigged(tmp_path, FakeEngine(document=UNDER_TAB_BAR))
    said = await r.actions.act(r.instance, CALLER, [step], snapshot="none")
    assert said.startswith("ok ") is played, said
    assert bool(r.engine.hid_events) is played
    if not played:
        assert "is under the tab bar" in said and "First scroll it into view" in said
    await r.actions.screenshots(r.instance, CALLER, width=400, region="e2")
    crop = r.engine.screenshots[-1][2]
    assert crop is not None and (round(crop.x), round(crop.y)) == (46, 783), "a picture of it is no touch"


def answering(name: str) -> FakeEngine:
    """A device reading a recorded screen and answering what is at each point it was asked, as it was recorded."""
    recorded = fixture_json(name)
    engine = FakeEngine(document=recorded["tree"])
    engine.points = recorded["points"]
    return engine


async def test_a_button_floating_in_front_of_a_tab_bar_is_tapped_and_a_row_under_the_bar_is_not(
    tmp_path: Path,
) -> None:
    # A probe app on iOS 26.5: an "add" button floats over the tab bar. Its line says it is covered -- accessibility
    # lists it, the bar and the rows in reading order -- and the hit test at its point answers the button itself.
    r = await rigged(tmp_path, answering("ax-floating-button-ios26.json"))
    look = (await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)).splitlines()
    assert look[1] == 'e1 button "Floating Add" (349,838) covered by tab bar'
    assert look[-1] == 'e15 button "Row 13" (201,859) covered by tab bar'
    said = await r.actions.act(r.instance, CALLER, [{"tap": "e1"}, {"tap": "e15"}], snapshot="none")
    assert said.splitlines() == [
        'ok tap e1 "Floating Add" (349,838)',
        'error step 2: e15 "Row 13" is under the tab bar: a touch at (201,859) would land on the tab bar. First '
        "scroll it into view with a swipe that starts on the content, not the bar, and then use e15 again",
    ]
    assert touches(r.engine.hid_events) == [("touch", (349, 838), "down"), ("touch", (349, 838), "up")]
    assert [[(round(x), round(y)) for x, y in batch] for batch in r.engine.asked] == [[(349, 838)], [(201, 859)]]


async def test_a_row_under_a_navigation_bar_no_reader_lists_is_refused_and_the_bars_own_button_is_tapped(
    tmp_path: Path,
) -> None:
    # Settings on iOS 26.5, scrolled up: it lists no navigation bar, and a tap where "Ready for Apple Intelligence" is
    # listed, under the bar, opened nothing -- the hit test there answers the bar's title. Above it, in the status
    # bar's strip, the hit test answers the status bar.
    r = await rigged(tmp_path, answering("ax-settings-under-navigation-bar-ios26.json"))
    look = (await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)).splitlines()
    assert look[2:4] == [
        'e1 button "Apple Account, Sign in to access your iCloud data, the App …" (201,12)',
        'e2 button "Ready for Apple Intelligence" (201,84)',
    ]
    for ref, where, bar in (("e2", "(201,84)", "navigation bar"), ("e1", "(201,12)", "status bar")):
        said = await r.actions.act(r.instance, CALLER, [{"tap": ref}], snapshot="none")
        assert f"is under the {bar}: a touch at {where} would land on the {bar}" in said, said
    assert r.engine.hid_events == []
    general = await rigged(tmp_path / "general", answering("ax-settings-back-button-ios26.json"))
    await general.actions.snapshot(general.instance, CALLER, mode="full", max_elements=120)
    said = await general.actions.act(general.instance, CALLER, [{"tap": "e1"}], snapshot="none")
    assert said == 'ok tap e1 "Settings" (38,84)', "the back button answers itself"


async def test_a_tap_near_the_top_goes_below_the_band_when_that_part_of_it_answers(tmp_path: Path) -> None:
    tall = {"elements": [framed("Application", "Tall", 0, 0, 402, 874, children=[
        framed("Button", "Card", 16, 40, 370, 140),
    ])]}  # fmt: skip
    engine = FakeEngine(document=tall)
    title = framed("Heading", "Title", 160, 74, 80, 21)
    engine.points = {"201,110": title, "201,148": framed("Button", "Card", 16, 40, 370, 140)}
    r = await rigged(tmp_path, engine)
    said = await r.actions.act(r.instance, CALLER, [{"tap": "e1"}], snapshot="none")
    assert said == 'ok tap e1 "Card" (201,148)'


@pytest.mark.parametrize(
    "answer",
    [
        None,
        ConnectorError("the companion is gone"),
        framed("Group", "", 0, 0, 402, 874),
        {"type": "Group"},
    ],
)
async def test_what_tells_nothing_of_what_is_in_front_plays_the_tap_as_the_snapshot_said(
    tmp_path: Path, answer: Any
) -> None:
    near_top = {"elements": [framed("Application", "Top", 0, 0, 402, 874, children=[
        framed("Button", "Row", 16, 60, 370, 52),
    ])]}  # fmt: skip
    engine = FakeEngine(document=near_top)
    if answer is not None:
        engine.points = {"201,86": answer}
    r = await rigged(tmp_path, engine)
    assert await r.actions.act(r.instance, CALLER, [{"tap": "e1"}], snapshot="none") == 'ok tap e1 "Row" (201,86)'
    floating = await rigged(tmp_path / "under", FakeEngine(document=UNDER_TAB_BAR))
    floating.engine.points = {"106,843": answer} if answer is not None else {}
    said = await floating.actions.act(floating.instance, CALLER, [{"tap": "e2"}], snapshot="none")
    assert "is under the tab bar" in said


async def test_a_device_slow_to_say_what_is_in_front_plays_the_tap_as_the_snapshot_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Slow(FakeEngine):
        async def elements_at(self, points: Any) -> list[dict[str, Any] | None]:
            await asyncio.sleep(3600)
            raise AssertionError("never answers")

    monkeypatch.setattr("sim_mirror.perception.in_front.BUDGET_S", 0.01)
    r = await rigged(tmp_path, Slow(document=UNDER_TAB_BAR))
    said = await r.actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")
    assert "is under the tab bar" in said


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


async def test_a_pause_the_host_says_a_person_made_holds_agents_off_and_stops_a_batch_under_way(
    tmp_path: Path,
) -> None:
    r = await rigged(tmp_path)
    pause = FakeInputPause()
    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=r.sleep, pause=pause)
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    pause.held.add(r.instance.udid)
    started = r.rig.clock.now
    with pytest.raises(ActionError, match="a person has paused agents on this device"):
        await actions.act(r.instance, CALLER, [{"tap": "e2"}])
    assert r.rig.clock.now >= started + PERSON_WAIT_S and r.engine.hid_events == []

    async def lifted(seconds: float) -> None:
        pause.held.clear()
        await r.sleep(seconds)

    actions._sleep = lifted
    assert (await actions.act(r.instance, CALLER, [{"tap": "e2"}], snapshot="none")).startswith("ok tap")
    actions._sleep = r.sleep
    played = len(r.engine.hid_events)

    async def paused_between(seconds: float) -> None:
        pause.held.add(r.instance.udid)
        await r.sleep(seconds)

    # A pause made while a batch runs stops it before its next gesture; what was played stays played.
    actions._sleep = paused_between
    answer = await actions.act(r.instance, CALLER, [{"tap": "e2"}, {"tap": "e3"}], snapshot="none", cursor=False)
    assert answer.splitlines()[1] == f"error step 2: {PAUSED}"
    assert answer.startswith("ok tap") and len(r.engine.hid_events) > played


@pytest.mark.parametrize(("second", "why"), [("e2", "is under the tab bar"), ("e4", PAUSED)])
async def test_a_pause_and_a_covered_ref_each_refuse_a_touch_before_anything_is_sent(
    tmp_path: Path, second: str, why: str
) -> None:
    r = await rigged(tmp_path, FakeEngine(document=UNDER_TAB_BAR))
    pause = FakeInputPause()

    async def paused_after(seconds: float) -> None:
        pause.held.add(r.instance.udid)
        await r.sleep(seconds)

    actions = AgentActions(r.rig.manager, r.rig.config, clock=r.rig.clock, sleep=paused_after, pause=pause)
    await actions.snapshot(r.instance, CALLER, mode="full", max_elements=120)
    said = (await actions.act(r.instance, CALLER, [{"tap": "e1"}, {"tap": second}], snapshot="none")).splitlines()
    assert said[0].startswith("ok tap e1") and said[1].startswith("error step 2:") and why in said[1]
    assert touches(r.engine.hid_events) == [("touch", (106, 765), "down"), ("touch", (106, 765), "up")]
    intents = [event for event in r.agent() if event.get("phase") == "intent" and event["gesture"]["kind"] == "tap"]
    assert len(intents) == 1, "the refused touch was never announced"


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
    assert text.splitlines()[0] == "note: the idb connector is switched off in this test"
    assert text.splitlines()[2:] == [
        'e1 text "Sign in" (200,410)',
        "1 line was read from the screen's pixels: text may be misread, and a ref taps its middle",
    ]
    failing = FakeTextRecognizer()
    failing.errors.append(TextRecognitionError("the text reader did not answer a reading of the screen"))
    unreadable = AgentActions(rig.manager, rig.config, pixels=OcrReaders(failing, supported=True))
    with pytest.raises(ActionError) as unread:
        await unreadable.snapshot(instance, CALLER, mode="full", max_elements=10)
    assert str(unread.value) == (
        "the text reader did not answer a reading of the screen; sim_screenshot still shows the screen\n"
        "note: the idb connector is switched off in this test"
    )
    rig.config.set(ocr_mode="off")
    assert actions.capabilities(instance.capabilities, rig.config.get(CALLER.scope)) == instance.capabilities
    with pytest.raises(ActionError, match=r"or perception\.ocr on; this device is shown through simctl"):
        await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    await rig.manager.stop(CALLER.scope)
    await actions.close()
    assert recognizer.closed


async def test_a_mirror_whose_picture_turned_with_the_device_reads_its_pixels_in_the_pictures_shape(
    tmp_path: Path,
) -> None:
    """simctl's screenshot turns with the device: measured, an iPhone 17 Pro described upright and turned after came
    out 2622x1206. Its pixels are read on a screen of that shape, which the snapshot and sim_device info say."""
    engine = FakeEngine()
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False), simctl=FakeConnector("simctl",
                    capabilities=VIEW_ONLY, engine=engine))  # fmt: skip
    instance = await rig.up()
    turned = Screen(2622, 1206, 874, 402, 3.0)
    deny = text_line("Don't Allow", 300, 240, 120, 30, screen=turned)
    recognizer = FakeTextRecognizer([deny])
    actions = AgentActions(rig.manager, rig.config, pixels=OcrReaders(recognizer, supported=True))
    rig.config.set(ocr_overlay=True)
    events = instance.events.subscribe()
    engine.shot = Shot(b"turned", 804, 370)
    lines = (await actions.snapshot(instance, CALLER, mode="full", max_elements=10)).splitlines()
    # Before, the line was put on the portrait screen described: (157,551) of 402x874pt.
    assert lines[0] == "note: the idb connector is switched off in this test", "why it is read from its pixels"
    assert lines[1].startswith("iOS 26.5 · 874x402pt · #") and lines[2] == 'e1 text "Don\'t Allow" (360,255)'
    assert " · 874x402pt @3x\n" in device_line(instance)
    # Asked at two pixels a point of the screen as last shown: 1748 across, which simctl shrinks the picture to.
    await actions.snapshot(instance, CALLER, mode="full", max_elements=10)
    assert [width for width, _, _ in engine.screenshots] == [804, 1748]
    # A viewer shows simctl's picture: the line is outlined where it was read in it.
    published = [events.get_nowait() for _ in range(events.qsize())]
    [box] = next(event["boxes"] for event in published if event["type"] == "screen_text")
    assert (box["x"], box["y"]) == (round(deny.box.x, 4), round(deny.box.y, 4))
    # Turned back upright, the screen is the one described again.
    engine.shot = Shot(JPEG, 402, 874)
    recognizer.lines = (SIGN_IN,)
    lines = (await actions.snapshot(instance, CALLER, mode="full", max_elements=10)).splitlines()
    assert lines[1].startswith("iOS 26.5 · 402x874pt · #") and lines[2] == 'e2 text "Sign in" (200,410)'
    assert instance.interface is not None and instance.interface.screen == instance.screen


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


async def test_a_device_that_types_text_whole_is_sent_it_between_the_tap_and_the_submit(tmp_path: Path) -> None:
    engine = FakeEngine()
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=engine, types_text=True))
    r = Rigged(rig, await rig.up())
    said = await r.actions.act(
        r.instance,
        CALLER,
        [{"type": "Caf\u00e9 \U0001f44b", "into": "e12", "clear": True, "submit": True}],
        snapshot="none",
    )
    assert engine.typed == ["Caf\u00e9 \U0001f44b"] and "and submit" in said
    kinds = touches(engine.hid_events)
    assert kinds[:2] == [("touch", (201, 822), "down"), ("touch", (201, 822), "up")]
    assert kinds[-2:] == [("key", 40, "down"), ("key", 40, "up")], "no paste, and the submit comes last"
    assert ("key", gestures.V_KEY, "down") not in kinds
    engine.hid_events.clear()
    await r.actions.act(r.instance, CALLER, [{"type": "plain"}], snapshot="none")
    assert engine.typed[-1] == "plain" and engine.hid_events == []
