# SPDX-License-Identifier: Apache-2.0
"""An agent turning a simulator: the device is turned through its connector, the screen read until the app on it lies
that way or it is clear it will not, and every agent's last look read again before a ref is touched."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import Capability, ConnectorError
from sim_mirror.core.actions import TURN_WAIT_S, ActionError, AgentActions, Turned
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import FULL_CONTROL, FakeConnector, FakeEngine, fixture_json
from sim_mirror.testing.rig import DeviceRig, scope

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude Code")
OTHER = Caller(scope("tp-1"), key="agent-2", title="Codex")
TURNABLE = FULL_CONTROL | {Capability.ROTATE}


def untimed(answer: str) -> str:
    """An answer without when each step was done, for a test of what was done."""
    return re.sub(r" \+\d+ms$", "", answer, flags=re.MULTILINE)


def portrait() -> dict[str, Any]:
    """The probe app upright, as the native helper reads it."""
    frame = {"x": 0, "y": 0, "width": 402, "height": 874}
    button = {"type": "Button", "label": "Alpha", "frame": {"x": 40, "y": 120, "width": 110, "height": 50}}
    app = {"type": "Application", "label": "Probe", "frame": frame, "children": [{**button, "children": []}]}
    return {"elements": [app], "modal": None, "truncated": False, "orientation": "portrait"}


@dataclass
class Rigged:
    rig: DeviceRig
    instance: DeviceInstance
    actions: AgentActions
    engine: FakeEngine
    slept: list[float]


async def rigged(folder: Path, *, capabilities: frozenset[Capability] = TURNABLE) -> Rigged:
    engine = FakeEngine(document=portrait())
    rig = DeviceRig(folder, idb=FakeConnector("native", engine=engine, capabilities=capabilities))
    instance = await rig.up()
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        rig.clock.advance(seconds)

    actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=sleep)
    return Rigged(rig, instance, actions, engine, slept)


async def test_a_turned_device_is_read_as_the_app_on_it_then_lies(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    turned_left = fixture_json("ax-probe-rotated-left-ios26.json")
    r.engine.turned = {"landscape_right": turned_left}
    before = await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)
    assert r.instance.orientation == "portrait" and 'button "Alpha"' in before
    turned = await r.actions.rotate(r.instance, CALLER, "landscape_right")
    assert r.engine.turns == ["landscape_right"] and r.slept == []
    assert turned.orientation == "landscape_right" == r.instance.orientation
    assert "874x402pt landscape" in turned.changes and 'button "Bravo"' in turned.changes
    # Turned back, the app is read upright again: the answer is what changed since this agent's last look.
    r.engine.turned["portrait"] = portrait()
    back = await r.actions.rotate(r.instance, CALLER, "portrait")
    assert back.orientation == "portrait" and r.instance.orientation == "portrait"
    assert "402x874pt" in back.changes


async def test_an_app_that_does_not_turn_that_way_is_waited_out_and_said_to_stay(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    await r.actions.snapshot(r.instance, CALLER, mode="full", max_elements=50)
    turned = await r.actions.rotate(r.instance, CALLER, "landscape_left")
    assert r.engine.turns == ["landscape_left"]
    assert turned.orientation == "portrait" and turned.changes.startswith("no change · #")
    assert sum(r.slept) == pytest.approx(TURN_WAIT_S)


async def test_every_agents_last_look_is_read_again_after_a_turn(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.engine.turned = {"landscape_right": fixture_json("ax-probe-rotated-left-ios26.json")}
    await r.actions.snapshot(r.instance, OTHER, mode="full", max_elements=50)
    await r.actions.rotate(r.instance, CALLER, "landscape_right")
    assert r.actions._remembered(r.instance, OTHER).stale
    assert not r.actions._remembered(r.instance, CALLER).stale


async def test_a_screen_unreadable_after_turning_is_said_after_the_turn(tmp_path: Path) -> None:
    r = await rigged(tmp_path)
    r.engine.turned = {"landscape_right": fixture_json("ax-probe-rotated-left-ios26.json")}
    # Once while the app turns, then read.
    r.engine.accessibility_errors = [ConnectorError("no frontmost application")]
    turned = await r.actions.rotate(r.instance, CALLER, "landscape_right")
    assert turned.orientation == "landscape_right" and len(r.slept) == 1
    r.engine.accessibility_errors = [ConnectorError("no frontmost application")] * 20
    gone = await r.actions.rotate(r.instance, CALLER, "portrait")
    assert gone == Turned(
        None,
        "the screen after turning could not be read: no frontmost application; sim_screenshot still shows the screen",
    )
    assert r.engine.turns == ["landscape_right", "portrait"]


async def test_a_device_that_cannot_be_turned_or_is_busy_is_refused_before_it_is_turned(tmp_path: Path) -> None:
    plain = await rigged(tmp_path / "plain", capabilities=FULL_CONTROL)
    with pytest.raises(ActionError, match="needs the native connector; this device is shown through native"):
        await plain.actions.rotate(plain.instance, CALLER, "landscape_left")
    r = await rigged(tmp_path / "busy")
    r.instance.busy = "running tests"
    with pytest.raises(ActionError, match="the device is busy: running tests"):
        await r.actions.rotate(r.instance, CALLER, "landscape_left")
    r.instance.busy = None
    r.engine.rotate_errors = [ConnectorError("the simulator has no PurpleWorkspacePort")]
    with pytest.raises(ActionError, match="no PurpleWorkspacePort") as refused:
        await r.actions.rotate(r.instance, CALLER, "landscape_left")
    assert isinstance(refused.value.__cause__, ConnectorError) and r.engine.turns == []


def floating(width: float, height: float, orientation: str, add: tuple[float, float]) -> dict[str, Any]:
    """An app with an "Add" button floating over its tab bar, lying `orientation` on the screen."""
    bar = {"type": "Group", "label": "Tab Bar", "frame": {"x": 0, "y": height - 53, "width": width, "height": 53}}
    button = {"type": "Button", "label": "Add", "frame": {"x": add[0], "y": add[1], "width": 60, "height": 40}}
    frame = {"x": 0, "y": 0, "width": width, "height": height}
    app = {"type": "Application", "label": "Probe", "frame": frame, "children": [button, bar]}
    return {"elements": [app], "modal": None, "truncated": False, "orientation": orientation}


async def test_what_is_in_front_of_a_ref_is_asked_where_it_lies_after_a_turn(tmp_path: Path) -> None:
    # Another agent looked while the app was upright; once the device is turned, its tap on the button asks the
    # device at the point the button now lies at, not where it was.
    r = await rigged(tmp_path)
    r.engine.document = floating(402, 874, "portrait", (310, 828))
    upright = await r.actions.snapshot(r.instance, OTHER, mode="full", max_elements=50)
    assert 'e1 button "Add" (340,848) covered by tab bar' in upright
    turned = floating(874, 402, "landscape_right", (780, 355))
    r.engine.turned = {"landscape_right": turned}
    await r.actions.rotate(r.instance, CALLER, "landscape_right")
    add = {"type": "Button", "label": "Add", "frame": {"x": 780, "y": 355, "width": 60, "height": 40}}
    r.engine.points = {"27,810": add}
    said = untimed(await r.actions.act(r.instance, OTHER, [{"tap": "e1"}], snapshot="none"))
    assert said == 'ok tap e1 "Add" (810,375)'
    assert r.engine.asked == [((27.0, 810.0),)]
    assert [(event.phase, round(event.x), round(event.y)) for event in r.engine.hid_events] == [
        ("down", 27, 810),
        ("up", 27, 810),
    ]
