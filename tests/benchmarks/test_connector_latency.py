# SPDX-License-Identifier: Apache-2.0
"""The connector latency benchmark on fake connectors: each measurement, each way it cannot be taken, and its report."""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterable
from typing import Any

import pytest

import connector_latency
from connector_latency import Bench, Result, Timing, main, table
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, ConnectorUnavailable, Crop, HidEvent, Shot
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.platform.simctl import Simctl
from sim_mirror.testing.fakes import BOOTED_UDID, FakeConnector, FakeEngine, FakeXcrun, ManualClock
from sim_mirror.testing.rig import VIEW_ONLY


class Ticking(ManualClock):
    """A clock that moves a millisecond each time it is read, so every measurement takes time."""

    def __call__(self) -> float:
        self.now += 0.001
        return self.now


class Tappable(FakeEngine):
    """A screen that changes after a tap, and settles between taps."""

    def __init__(self, *, changes: bool = True) -> None:
        super().__init__()
        self.changes = changes
        self.tapped = 0

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        async for event in events:
            self.hid_events.append(event)
            if event.phase == "up" and event.y > 100:
                self.tapped += 1

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.screenshots.append((max_width, quality, crop))
        marker = self.tapped if self.changes else 0
        return Shot(b"\xff\xd8" + bytes([marker]), 402, 874)


def bench(clock: Ticking, rounds: int = 2) -> tuple[Bench, list[str]]:
    prepared: list[str] = []

    async def prepare() -> None:
        prepared.append("settings")

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return Bench(BOOTED_UDID, SimConfig.defaults(), rounds, prepare, clock, sleep), prepared


async def test_a_connector_that_can_do_everything_is_measured_everywhere() -> None:
    clock = Ticking()
    measured, prepared = bench(clock)
    result = await measured.run(FakeConnector("native", engine=Tappable()))
    assert result.notes == [] and result.attach_ms is not None and result.first_frame_ms is not None
    assert result.screenshot_900 is not None and result.screenshot_900.runs == 2
    assert result.screenshot_160 is not None and result.snapshot is not None and result.snapshot_elements
    assert result.input is not None and result.tap_to_change is not None and result.tap_to_change.runs == 2
    assert prepared == ["settings"] * 3


async def test_a_view_only_connector_is_measured_only_where_it_can_be() -> None:
    result = await bench(Ticking())[0].run(FakeConnector("simctl", capabilities=VIEW_ONLY))
    assert result.screenshot_900 is not None
    assert (result.first_frame_ms, result.snapshot, result.input, result.tap_to_change) == (None, None, None, None)


async def test_what_keeps_a_connector_from_being_measured_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Ticking()
    measured = bench(clock)[0]
    unavailable = await measured.run(FakeConnector("idb", available=False, reasons=("no companion.",)))
    assert unavailable.notes == ["not available here: no companion."] and unavailable.attach_ms is None
    refused = await measured.run(FakeConnector("idb", fail=ConnectorUnavailable("claimed")))
    assert refused.notes == ["could not attach: claimed"]
    broken = FakeEngine()
    broken.screenshot_errors = [ConnectorError("the helper went away")]
    stopped = await measured.run(FakeConnector("native", engine=broken))
    assert stopped.notes == ["stopped: the helper went away"] and stopped.attach_ms is not None
    monkeypatch.setattr(connector_latency, "FIRST_FRAME_TIMEOUT_S", 0.01)
    silent = Tappable()
    silent.chunks = []
    assert (await measured.run(FakeConnector("native", engine=silent))).first_frame_ms is None


async def test_a_tap_that_changes_nothing_or_a_settings_without_general_is_noted() -> None:
    clock = Ticking()
    still = await bench(clock, rounds=1)[0].run(FakeConnector("idb", engine=Tappable(changes=False)))
    assert still.notes == ["a tap on General did not change the screen within 5s"] and still.tap_to_change is None
    elsewhere = Tappable()
    elsewhere.document = {
        "elements": [{"type": "Button", "label": "Back", "frame": {"x": 1, "y": 1, "width": 9, "height": 9}}]
    }
    lost = await bench(Ticking(), rounds=1)[0].run(FakeConnector("idb", engine=elsewhere))
    assert lost.notes == ["Settings showed no General row to tap"] and lost.tap_to_change is None


async def test_a_screen_that_never_settles_is_tapped_anyway() -> None:
    class Restless(Tappable):
        async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
            self.screenshots.append((max_width, quality, crop))
            return Shot(b"\xff\xd8" + len(self.screenshots).to_bytes(4, "big"), 402, 874)

    result = await bench(Ticking(), rounds=1)[0].run(FakeConnector("native", engine=Restless()))
    assert result.tap_to_change is not None


def test_timings_are_milliseconds_and_the_table_says_what_was_not_measured() -> None:
    assert Timing.of([]) is None
    assert Timing.of([0.010, 0.020, 0.030]) == Timing(20.0, 30.0, 3)
    rows = table(
        [
            Result("native", attach_ms=210.0, input=Timing(0.3, 3.1, 10), snapshot_elements=15),
            Result("simctl", notes=["no input"]),
        ]
    )
    assert "| native | 210.0 | – | – | – | – (15) | 0.3 / 3.1 | – |" in rows
    assert rows.endswith("\n\n- simctl: no input")
    assert "\n\n" not in table([Result("idb")])


def run_main(
    *argv: str, registry: ConnectorRegistry | None = None, xcrun: FakeXcrun | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    fake = xcrun or FakeXcrun()
    clock = Ticking()

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    code = main(
        list(argv),
        registry=lambda: registry or ConnectorRegistry([FakeConnector("native", engine=Tappable())]),
        simctl=lambda developer_dir: Simctl(fake, developer_dir=developer_dir),
        clock=clock,
        sleep=sleep,
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_the_command_measures_the_connectors_named_in_order_and_prints_a_table_or_json() -> None:
    xcrun = FakeXcrun()
    code, out, _err = run_main("--device", BOOTED_UDID, "--connectors", "native, missing", "--rounds", "1", xcrun=xcrun)
    assert code == 0 and "| native |" in out and "- missing: no such connector" in out
    assert ("simctl", "launch", "--terminate-running-process", BOOTED_UDID, "com.apple.Preferences") in xcrun.argv()
    code, out, _err = run_main("--device", BOOTED_UDID, "--connectors", "native", "--rounds", "1", "--json")
    reported: list[dict[str, Any]] = json.loads(out)
    assert code == 0 and reported[0]["connector"] == "native" and reported[0]["input"]["runs"] == 1


def test_the_command_refuses_no_rounds_and_says_when_settings_cannot_be_launched() -> None:
    assert run_main("--device", BOOTED_UDID, "--rounds", "0")[::2] == (2, "--rounds is at least 1\n")
    failing = FakeXcrun().on("simctl", "launch", rc=1, err="Unable to lookup in current state: Shutdown")
    code, _out, err = run_main("--device", BOOTED_UDID, xcrun=failing)
    assert code == 1 and err.startswith(f"Settings could not be launched on {BOOTED_UDID}")


def test_the_real_registry_and_simctl_are_this_macs(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "native" in connector_latency.default_registry().names()
    assert isinstance(connector_latency._simctl("/Applications/Xcode.app/Contents/Developer"), Simctl)
