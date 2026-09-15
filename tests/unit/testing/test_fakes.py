# SPDX-License-Identifier: Apache-2.0
"""The shipped fakes behave as the real things do, so a host's tests can trust them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, Crop, HidEvent
from sim_mirror.connectors.simctl.capture import jpeg_size
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import (
    BOOTED_UDID,
    JPEG,
    SCREEN,
    FakeConnector,
    FakeEngine,
    FakeLauncher,
    FakeProcess,
    FakeXcrun,
    ManualClock,
    MemoryStateStore,
    StaticConfig,
    fixture_json,
    fixture_udid,
    made,
    no_wait,
)


async def test_fake_xcrun_records_calls_and_answers_from_the_latest_matching_prefix() -> None:
    fake = FakeXcrun().on("simctl", out="first").on("simctl", "list", out="second")
    fake.on("simctl", "boot", then=lambda args: XcrunResult(0, " ".join(args), ""))
    assert (await fake("simctl", "list", "devices")).out == "second"
    assert (await fake("simctl", "boot", "U")).out == "simctl boot U"
    assert (await fake("simctl", "shutdown")).out == "first"
    assert (await fake("xcodebuild", "-version", timeout=5, cwd=Path("/tmp"))) == XcrunResult(0, "", "")
    assert fake.argv()[-1] == ("xcodebuild", "-version") and fake.calls[-1].cwd == "/tmp"
    assert (await fake.with_lists()("simctl", "list", "runtimes", "-j")).out.startswith("{")


async def test_a_fake_process_runs_until_it_is_finished() -> None:
    proc = FakeProcess(pid=7)
    waiter = asyncio.ensure_future(proc.wait())
    await no_wait(1)
    assert not waiter.done() and proc.returncode is None
    proc.finish(3)
    assert await waiter == 3


def test_a_manual_clock_moves_by_hand() -> None:
    clock = ManualClock()
    clock.advance(2.5)
    assert clock() == 102.5


def test_fixtures_name_their_devices() -> None:
    assert fixture_udid("iPhone 17 Pro") == BOOTED_UDID
    assert "devices" in fixture_json("simctl-devices.json") and made(2).endswith("000000000002")


def test_static_config_gives_every_scope_the_same_config_unless_one_has_its_own() -> None:
    config = StaticConfig(stream_fps=24)
    demo, other = Scope.named("demo"), Scope.named("other")
    config.set(max_booted=4)
    config.set_for("demo", agent_cursor=False)
    assert config.get(other).stream_fps == 24 and config.get(other).max_booted == 4
    assert config.get(demo).agent_cursor is False and config.get(demo).stream_fps == 24
    assert StaticConfig(SimConfig.defaults("embedded")).get(demo).enabled is False


async def test_a_fake_engine_answers_records_and_fails_when_told() -> None:
    engine = FakeEngine(describe_error=ConnectorError("gone"))
    with pytest.raises(ConnectorError, match="gone"):
        await engine.describe()
    engine.describe_error = None
    assert await engine.describe() == SCREEN
    engine.screenshot_errors.append(ConnectorError("busy"))
    with pytest.raises(ConnectorError, match="busy"):
        await engine.screenshot(max_width=400, quality=70)
    assert await engine.screenshot(max_width=400, quality=70, crop=Crop(0, 0, 1, 1)) == engine.shot
    assert engine.screenshots[-1] == (400, 70, Crop(0, 0, 1, 1))
    engine.accessibility_errors.append(ConnectorError("unreadable"))
    with pytest.raises(ConnectorError, match="unreadable"):
        await engine.accessibility()
    assert "elements" in await engine.accessibility()
    engine.chunks = [b"one", b"two"]
    stream = engine.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=0)
    assert [await stream.__anext__(), await stream.__anext__()] == [b"one", b"two"]
    waiting = asyncio.ensure_future(stream.__anext__())
    await no_wait(0)
    waiting.cancel()

    async def events() -> AsyncIterator[HidEvent]:
        yield HidEvent.touch("down", 1, 2)

    await engine.hid(events())
    await engine.close()
    assert engine.hid_events == [HidEvent.touch("down", 1, 2)] and engine.closed
    assert jpeg_size(JPEG) == (402, 874)


async def test_a_fake_launcher_holds_fails_and_stops_when_told() -> None:
    held = FakeLauncher(hold=True)
    starting = asyncio.ensure_future(held.start("/bin/c", BOOTED_UDID))
    await no_wait(0)
    assert not starting.done() and held.started == [("/bin/c", BOOTED_UDID)]
    assert held.release is not None
    held.release.set()
    companion = await starting
    await held.stop(companion)
    assert held.stopped == [BOOTED_UDID] and not companion.alive and await held.reap_orphans() == 2
    with pytest.raises(ConnectorUnavailable, match="nope"):
        await FakeLauncher(fail=ConnectorUnavailable("nope")).start("/bin/c", BOOTED_UDID)


async def test_a_fake_connector_holds_fails_refuses_and_hands_roles_by_capability() -> None:
    config = SimConfig.defaults()
    held = FakeConnector(hold=True, fps_limit=4)
    attaching = asyncio.ensure_future(held.attach(BOOTED_UDID, config))
    await no_wait(0)
    assert not attaching.done() and held.release is not None
    held.release.set()
    session = await attaching
    assert session.input is held.engine and session.reader is held.engine and session.fps_limit == 4
    held.alive = False
    assert not session.alive
    await session.close()
    assert held.closed == [BOOTED_UDID] and await held.reap_orphans() == 0 and held.reaped == 1
    with pytest.raises(ConnectorUnavailable, match="broken"):
        await FakeConnector(fail=ConnectorUnavailable("broken")).attach(BOOTED_UDID, config)
    off = FakeConnector("idb", available=False)
    assert (await off.probe(config)).reasons == ("the idb connector is switched off in this test",)
    with pytest.raises(ConnectorUnavailable, match="switched off") as refused:
        await off.attach(BOOTED_UDID, config)
    assert refused.value.status == 409
    view_only = await FakeConnector(capabilities=frozenset({Capability.SCREENSHOT})).attach(BOOTED_UDID, config)
    assert view_only.input is None and view_only.reader is None


def test_memory_state_store_keeps_everything_under_its_root(tmp_path: Path) -> None:
    store = MemoryStateStore(tmp_path)
    scope = Scope(id="ws:a:b", group="a", label="a")
    paths = [store.devices_file(scope), store.builds_dir(scope), store.derived_data(scope), store.run_dir(),
             store.log_dir(), store.claims_dir()]  # fmt: skip
    assert all(path.is_relative_to(tmp_path) for path in paths) and store.owner_tag == "SimMirrorTest"
    assert store.builds_dir(scope).name == "ws_a_b" and store.ensure_dir(tmp_path / "x").is_dir()
