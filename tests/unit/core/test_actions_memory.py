# SPDX-License-Identifier: Apache-2.0
"""What an agent last saw of a device is kept for its diffs -- and let go when the device ends, so a daemon that runs
for weeks does not keep a snapshot for every agent session it ever had."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.core.actions import AgentActions
from sim_mirror.seams import Caller
from sim_mirror.testing.rig import DeviceRig, scope


async def test_what_agents_last_saw_on_a_device_is_let_go_when_the_device_ends(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    actions = AgentActions(rig.manager, rig.config)
    instance = await rig.up()
    await actions.read(instance, Caller(scope(), "token-1", "codex"), 120)
    assert list(actions._memory) == [(instance.udid, "token-1")]
    await rig.manager.stop(scope())
    assert actions._memory == {}
