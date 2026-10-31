# SPDX-License-Identifier: Apache-2.0
"""The part of the screen a recording keeps: a ref's element where its edges are, or a rectangle in points -- as
fractions of the device's own screen, as the recording's touches are."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sim_mirror.core.actions import ActionError, AgentActions
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import FakeConnector, FakeEngine, no_wait
from sim_mirror.testing.rig import DeviceRig, scope

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude")


async def test_a_recordings_region_is_a_part_of_the_device_s_screen(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=FakeEngine()))
    instance = await rig.up()
    actions = AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait)
    assert await actions.region(instance, CALLER, None, 120) is None
    assert await actions.region(instance, CALLER, {"x": 0, "y": 0, "w": 201, "h": 437}, 120) == (0.0, 0.0, 0.5, 0.5)
    # Reaching past the screen's edge keeps what is on it.
    assert await actions.region(instance, CALLER, {"x": 201, "y": 437, "w": 900, "h": 900}, 120) == (
        0.5,
        0.5,
        0.5,
        0.5,
    )
    with pytest.raises(ActionError, match=r"region \{'x': 500, .* is off the 402x874pt screen"):
        await actions.region(instance, CALLER, {"x": 500, "y": 0, "w": 10, "h": 10}, 120)
    with pytest.raises(ActionError, match="region is a ref"):
        await actions.region(instance, CALLER, {"x": 0}, 120)
    await actions.snapshot(instance, CALLER, mode="full", max_elements=120)
    memory = actions._remembered(instance, CALLER)
    assert memory.snapshot is not None
    general = memory.snapshot.element("e2")
    assert general is not None and general.frame is not None
    frame = general.frame
    x, y, w, h = await actions.region(instance, CALLER, "e2", 120)  # type: ignore[misc]
    assert (x, y, w, h) == (
        round(max(0.0, frame.x) / 402, 5),
        round(frame.y / 874, 5),
        round((min(402.0, frame.x + frame.width) - max(0.0, frame.x)) / 402, 5),
        round(frame.height / 874, 5),
    )
    # An element its reader gave no edges is the square a screenshot of it takes.
    unbounded = tuple(replace(element, frame=None) for element in memory.snapshot.elements)
    memory.snapshot = replace(memory.snapshot, elements=unbounded)
    x, y, w, h = await actions.region(instance, CALLER, "e2", 120)  # type: ignore[misc]
    assert (round(x * 402), round(y * 874), round(w * 402), round(h * 874)) == (141, 259, 120, 120)
