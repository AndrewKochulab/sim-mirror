# SPDX-License-Identifier: Apache-2.0
"""Setting WebDriverAgent up for a team and an Xcode: one setup at a time, the devices each build was made for, a
checkout of a person's own, what went wrong said, and a setup under way ended with the host."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sim_mirror.connectors.iphone.wda_setup import DEVICES_FILE, Setup, WdaSetup, derived_for, devices_built_for
from sim_mirror.connectors.iphone.wda_source import MJPEG_ON_LOOPBACK, PROJECT
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun
from sim_mirror.testing.wda import ARCHIVE, SERVER, TEST_RELEASE, builds_wda

TEAM = "TESTTEAM01"
XCODE = "/Applications/Xcode.app/Contents/Developer"


def setup_in(root: Path, xcrun: Any) -> WdaSetup:
    return WdaSetup(root=lambda: root, fetch=lambda url: ARCHIVE, xcrun=xcrun, release=TEST_RELEASE)


async def test_a_setup_for_any_device_builds_once_and_is_answered_while_it_runs(tmp_path: Path) -> None:
    xcrun = builds_wda(FakeXcrun())
    setups = setup_in(tmp_path, xcrun)
    ready: list[str] = []
    setups.on_ready = ready.append
    said: list[str] = []
    first = setups.start(TEAM, XCODE, None, say=said.append)
    assert setups.start(TEAM, XCODE, PHONE_UDID) is first, "one setup per team and Xcode at a time"
    await setups.wait(first)
    assert first.state == "ready" and first.said == f"WebDriverAgent is built for team {TEAM}"
    assert said[0].startswith("fetching WebDriverAgent 9.9.9") and "any device the team's profile covers" in said[1]
    assert ready == [] and not (derived_for(tmp_path, TEAM, XCODE) / DEVICES_FILE).exists()
    assert setups.agreed(TEAM) and not setups.agreed("OTHERTEAM1")
    assert setups.built_for(TEAM, XCODE, PHONE_UDID) is None, "not made for this device"
    again = setups.start(TEAM, XCODE, PHONE_UDID)
    assert again is not first
    await setups.wait(again)
    assert setups.built_for(TEAM, XCODE, PHONE_UDID) is not None and ready == [PHONE_UDID]
    assert devices_built_for(derived_for(tmp_path, TEAM, XCODE)) == {PHONE_UDID}


async def test_a_person_s_own_checkout_is_built_as_it_is_and_one_without_the_change_is_refused(tmp_path: Path) -> None:
    own = tmp_path / "own"
    (own / PROJECT).mkdir(parents=True)
    server = own / MJPEG_ON_LOOPBACK.path
    server.parent.mkdir(parents=True)
    server.write_bytes(SERVER)
    xcrun = builds_wda(FakeXcrun())
    setups = setup_in(tmp_path / "wda", xcrun)
    refused = await setups.wait(setups.start(TEAM, XCODE, PHONE_UDID, str(own)))
    assert refused.state == "failed" and "does not keep the screen stream" in refused.said and xcrun.calls == []
    server.write_text(server.read_text().replace(MJPEG_ON_LOOPBACK.old, MJPEG_ON_LOOPBACK.new))
    built = await setups.wait(setups.start(TEAM, XCODE, PHONE_UDID, str(own)))
    assert built.state == "ready" and str(own / PROJECT) in xcrun.calls[-1].args


async def test_what_breaks_a_setup_is_said_and_one_under_way_ends_with_the_host(tmp_path: Path) -> None:
    async def broken(*args: str, **options: Any) -> XcrunResult:
        raise RuntimeError("xcodebuild vanished")

    unbuildable = setup_in(tmp_path, broken)
    failed = await unbuildable.wait(unbuildable.start(TEAM, XCODE, PHONE_UDID))
    assert failed.state == "failed" and failed.said == "RuntimeError: xcodebuild vanished"
    assert (await WdaSetup().wait(Setup(TEAM, XCODE, None))).state == "building", "a setup never started is as it was"
    started = asyncio.Event()

    async def forever(*args: str, **options: Any) -> XcrunResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("never answers")

    setups = setup_in(tmp_path, forever)
    done = await setups.wait(setups.start(TEAM, "/Applications/Xcode27.app", None, str(tmp_path / "missing")))
    assert done.state == "failed" and "holds no WebDriverAgent.xcodeproj" in done.said
    running = setups.start(TEAM, XCODE, PHONE_UDID)
    await started.wait()
    await setups.shutdown()
    assert running.task is not None and running.task.cancelled()
