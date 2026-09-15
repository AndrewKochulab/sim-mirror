# SPDX-License-Identifier: Apache-2.0
"""The doctor's real tap: a tap on Settings' General row that reaches the device, one that is swallowed, and every way
it cannot be tried."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.core.runtime import Runtime
from sim_mirror.doctor.tap import SWALLOWED, TAP_SCOPE, check_tap
from sim_mirror.testing.fakes import BOOTED_UDID, FakeConnector, FakeEngine, fixture_json, fixture_udid, made
from sim_mirror.testing.rig import DeviceRig

SETTINGS = fixture_json("ax-settings-interactable.json")


def general_page() -> dict[str, Any]:
    page = copy.deepcopy(SETTINGS)
    page["elements"][0]["children"].append(
        {"type": "StaticText", "label": "About", "frame": {"x": 20, "y": 120, "width": 200, "height": 44}}
    )
    return page


class Settings(FakeEngine):
    """Settings, whose General row opens General's page -- unless touches never arrive."""

    def __init__(self, *, swallowed: bool = False, general: bool = True) -> None:
        super().__init__()
        self.swallowed = swallowed
        if not general:
            self.document = copy.deepcopy(SETTINGS)
            children = self.document["elements"][0]["children"]
            children[:] = [node for node in children if node.get("label") != "General"]

    async def accessibility(self) -> dict[str, Any]:
        if self.hid_events and not self.swallowed:
            return general_page()
        return await super().accessibility()


class Tapping:
    def __init__(self, root: Path, **connector: Any) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.rig = DeviceRig(root, idb=FakeConnector("idb", **connector))
        self.runtime = Runtime.build(
            config=self.rig.config,
            state=self.rig.state,
            policy=self.rig.policy,
            copy=self.rig.copy,
            registry=self.rig.registry,
            claims=self.rig.claims,
            xcrun=self.rig.xcrun,
            clock=self.rig.clock,
            sleep=self.tick,
        )

    async def tick(self, seconds: float) -> None:
        self.rig.clock.advance(seconds)
        await asyncio.sleep(0)

    async def tap(self, device: str | None = None) -> Any:
        result = await check_tap(self.runtime, device, self.tick)
        assert self.runtime.manager.instance(TAP_SCOPE) is None
        return result


async def test_a_tap_that_reaches_the_booted_simulator_opens_general(tmp_path: Path) -> None:
    here = Tapping(tmp_path, engine=Settings())
    result = await here.tap()
    assert (result.status, result.detail) == ("ok", "a tap on General reached iPhone 17 Pro through idb")
    assert ("simctl", "launch", "--terminate-running-process", BOOTED_UDID, "com.apple.Preferences") in here.rig.argv()


async def test_a_tap_that_changes_nothing_says_input_was_swallowed_and_what_to_do(tmp_path: Path) -> None:
    result = await Tapping(tmp_path, engine=Settings(swallowed=True)).tap()
    assert (
        result.status == "fail" and result.detail == "a tap on General did not change iPhone 17 Pro's screen within 3s"
    )
    assert result.fix == SWALLOWED and "Device Hub" in result.fix


async def test_the_device_named_is_tapped_and_without_a_booted_one_the_doctor_uses_its_own(tmp_path: Path) -> None:
    named = Tapping(tmp_path / "named", engine=Settings())
    other = fixture_udid("iPhone 17 Pro Max")
    assert (await named.tap(other)).status == "ok"
    assert ("simctl", "launch", "--terminate-running-process", other, "com.apple.Preferences") in named.rig.argv()
    own = Tapping(tmp_path / "own", engine=Settings())
    for devices in own.rig.devices["devices"].values():
        for device in devices:
            device["state"] = "Shutdown"
    assert (await own.tap()).status == "ok"
    assert ("simctl", "launch", "--terminate-running-process", made(1), "com.apple.Preferences") in own.rig.argv()


async def test_a_tap_that_cannot_be_tried_says_why(tmp_path: Path) -> None:
    assert (await check_tap(None, None, asyncio.sleep)).detail == "skipped (--no-tap)"
    view_only = await Tapping(tmp_path / "view-only", available=False).tap()
    assert view_only.status == "warn" and "shown through simctl, which cannot take touches" in view_only.detail
    no_general = await Tapping(tmp_path / "no-general", engine=Settings(general=False)).tap()
    assert no_general.status == "fail" and no_general.detail.endswith("but its General row was not on screen to tap")
    failing = await Tapping(tmp_path / "failing", fail=ConnectorUnavailable("no companion")).tap()
    assert (failing.status, failing.detail) == ("fail", "the simulator failed: no companion")
    stuck = Tapping(tmp_path / "stuck", hold=True)
    assert (await stuck.tap()).detail == "the simulator was still booting after 120s"
    await stuck.runtime.close()
    off = Tapping(tmp_path / "off")
    off.rig.config.set(enabled=False)
    refused = await check_tap(off.runtime, None, off.tick)
    assert (refused.status, refused.detail) == (
        "fail",
        "The iOS Simulator is off for this project (`sim-mirror config`).",
    )
