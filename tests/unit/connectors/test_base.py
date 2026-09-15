# SPDX-License-Identifier: Apache-2.0
"""What every connector shares: the protocol's capabilities, input events, reports and sessions."""

from __future__ import annotations

import pytest

from sim_mirror.connectors.base import (
    INPUT_CAPABILITIES,
    Capability,
    ConnectorReport,
    ConnectorUnavailable,
    DeviceSession,
    HidEvent,
)
from sim_mirror.protocol import CAPABILITIES
from sim_mirror.testing.fakes import FakeEngine


def test_the_capabilities_are_the_protocols_own_in_its_order() -> None:
    assert tuple(capability.value for capability in Capability) == CAPABILITIES
    assert Capability("input_touch") is Capability.INPUT_TOUCH and Capability.INPUT_TOUCH == "input_touch"
    assert all(capability.value.startswith("input_") for capability in INPUT_CAPABILITIES)


def test_input_events_are_made_by_what_they_are() -> None:
    assert HidEvent.touch("down", 1.5, 2.5) == HidEvent("touch", "down", x=1.5, y=2.5)
    assert HidEvent.press("home", "up").button == "home"
    assert HidEvent.key(40, "down").code == 40
    with pytest.raises(ValueError, match="not a button"):
        HidEvent.press("volume", "down")


def test_a_report_says_what_was_found_as_plain_data() -> None:
    report = ConnectorReport(
        "idb", True, frozenset({Capability.SCREENSHOT, Capability.INPUT_KEY}), {"idb_companion": "/x"}
    )
    assert report.to_dict() == {
        "name": "idb",
        "available": True,
        "capabilities": ["input_key", "screenshot"],
        "versions": {"idb_companion": "/x"},
        "reasons": [],
    }
    assert ConnectorReport("simctl", False, reasons=("no xcrun",)).to_dict()["reasons"] == ["no xcrun"]
    assert ConnectorUnavailable("gone").status == 502 and ConnectorUnavailable("busy", 409).status == 409


async def test_a_session_is_alive_until_closed_or_its_helper_stops_and_closes_once() -> None:
    closes: list[int] = []
    helper = {"running": True}

    async def close() -> None:
        closes.append(1)

    session = DeviceSession(
        connector="fake",
        capabilities=frozenset({Capability.SCREENSHOT}),
        screen=FakeEngine(),
        is_alive=lambda: helper["running"],
        on_close=close,
    )
    assert session.alive and session.can(Capability.SCREENSHOT) and not session.can(Capability.INPUT_TOUCH)
    helper["running"] = False
    assert not session.alive
    helper["running"] = True
    await session.close()
    await session.close()
    assert closes == [1] and not session.alive
    plain = DeviceSession(connector="fake", capabilities=frozenset(), screen=FakeEngine())
    assert plain.alive and plain.fps_limit is None
    await plain.close()
    assert not plain.alive
