# SPDX-License-Identifier: Apache-2.0
"""The shipped fakes behave as the real things do, so a host's tests can trust them."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import (
    BOOTED_UDID,
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


def test_memory_state_store_keeps_everything_under_its_root(tmp_path: Path) -> None:
    store = MemoryStateStore(tmp_path)
    scope = Scope(id="ws:a:b", group="a", label="a")
    paths = [store.devices_file(scope), store.builds_dir(scope), store.derived_data(scope), store.run_dir(),
             store.log_dir(), store.claims_dir()]  # fmt: skip
    assert all(path.is_relative_to(tmp_path) for path in paths) and store.owner_tag == "sim-mirror-test"
    assert store.builds_dir(scope).name == "ws_a_b" and store.ensure_dir(tmp_path / "x").is_dir()
