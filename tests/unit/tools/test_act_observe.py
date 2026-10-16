# SPDX-License-Identifier: Apache-2.0
"""`sim_act` in a scope in observe mode: what reads like it changes something is refused unless that call confirms."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.core.actions import AgentActions
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, text

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")


async def use(rig: DeviceRig, arguments: object) -> Result:
    ctx = ToolContext(
        manager=rig.manager,
        actions=AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait),
        caller=CALLER,
        config=rig.config.get(CALLER.scope),
        copy=rig.copy,
        sleep=no_wait,
    )
    return await ToolRegistry().call("sim_act", arguments, ctx)


async def test_observe_mode_refuses_a_tap_its_words_name_until_the_call_confirms_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    rig.config.set(agent_observe=True, agent_observe_words="general")
    refused = await use(rig, {"steps": [{"tap": "e2"}], "snapshot": "none"})
    assert refused["isError"] is True
    assert str(refused["content"][0]["text"]).startswith('error step 1: e2 "General" (201,319) looks like it changes')
    assert rig.idb.engine.hid_events == []
    confirmed = await use(rig, {"steps": [{"tap": "e2"}], "snapshot": "none", "confirm": True})
    assert confirmed == text('ok tap e2 "General" (201,319), confirmed ("general")')
    # Confirming is for the call that says it: the next one is refused again.
    assert (await use(rig, {"steps": [{"tap": "e2"}], "snapshot": "none"}))["isError"] is True
    assert await use(rig, {"steps": [{"tap": "e2"}], "confirm": "yes"}) == text("confirm is true or false", error=True)
    rig.config.set(agent_observe=False)
    assert await use(rig, {"steps": [{"tap": "e2"}], "snapshot": "none"}) == text('ok tap e2 "General" (201,319)')
