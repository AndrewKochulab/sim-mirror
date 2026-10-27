# SPDX-License-Identifier: Apache-2.0
"""The doctor on real devices and recording: what is set up, and what a person does when it is not."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.iphone.wda_setup import derived_for
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.real_devices import (
    CAMERA_FIX,
    TOUCH_FIX,
    check_cable_screen,
    check_camera,
    check_real_devices,
    check_recording,
    check_touch_now,
    check_webdriveragent,
)
from sim_mirror.platform.keychain import Team
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun, fixture, fixture_json
from sim_mirror.testing.wda import left_built

XCODE = "/Applications/Xcode27.app/Contents/Developer"
PEM = fixture("development-certificate.pem")


def context(
    tmp_path: Path, *features: str, xcrun: FakeXcrun | None = None, keychain: str = "", **settings: Any
) -> DoctorContext:
    helper = tmp_path / "sim-mirror-helper"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    said = json.dumps({"version": __version__, "wire": 1, "core_simulator": None, "features": list(features)})

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        if tuple(argv[:2]) == ("security", "find-certificate"):
            return 0, keychain
        return (0, said) if tuple(argv) == (str(helper), "version") else (1, "")

    settings = {"developer_dir": XCODE, "recording_folder": str(tmp_path / "Movies"), **settings}
    config = SimConfig.defaults().with_values(**settings)
    return DoctorContext(
        config=config,
        registry=ConnectorRegistry([]),
        run=run,
        xcrun=xcrun or FakeXcrun().with_devicectl(),
        env={"SIM_MIRROR_STATE_DIR": str(tmp_path / "state")},
        helper_candidates=lambda: (helper,),
    )


async def test_real_devices_are_listed_with_what_stands_in_their_way(tmp_path: Path) -> None:
    off = await check_real_devices(context(tmp_path, real_devices=False))
    assert off.status == "ok" and off.detail.startswith("off;")
    found = await check_real_devices(context(tmp_path))
    assert (
        found.status == "ok"
        and found.detail
        == "Second iPhone, iOS 27.0 · iPhone 17 Pro (network); Test iPhone, iOS 26.3 · iPhone 14 Pro (usb)"
    )
    listing = fixture_json("devicectl-devices.json")
    for device in listing["result"]["devices"]:
        device["deviceProperties"]["developerModeStatus"] = "disabled"
        device.get("properties", {}).get("state", {})["developerModeStatus"] = "disabled"
    blocked = FakeXcrun().with_devicectl().on("devicectl", "-q", "list", "devices", out=json.dumps(listing))
    warned = await check_real_devices(context(tmp_path, xcrun=blocked))
    assert warned.status == "warn" and "Developer Mode" in warned.fix
    none = {"info": {"outcome": "success"}, "result": {"devices": []}}
    empty = FakeXcrun().with_devicectl().on("devicectl", "-q", "list", "devices", out=json.dumps(none))
    assert (await check_real_devices(context(tmp_path, xcrun=empty))).detail.startswith("none connected")
    broken = FakeXcrun().on("devicectl", rc=1, err="CoreDevice is not running")
    failed = await check_real_devices(context(tmp_path, xcrun=broken))
    assert failed.status == "warn" and "CoreDevice is not running" in failed.detail


async def test_the_cable_screen_needs_a_helper_that_captures(tmp_path: Path) -> None:
    assert (await check_cable_screen(context(tmp_path, real_devices=False))).detail == "off with real devices"
    shots = await check_cable_screen(context(tmp_path, "capture", real_devices_screen="screenshot"))
    assert shots.detail == "not used (real_devices.screen is screenshot)"
    old = await check_cable_screen(context(tmp_path, "render"))
    assert old.status == "warn" and "cannot show a cabled device's screen" in old.detail
    ready = await check_cable_screen(context(tmp_path, "capture"))
    assert ready.status == "ok" and "the Camera" in ready.detail and ready.fix == ""


async def test_webdriveragent_is_checked_for_its_team_and_its_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await check_webdriveragent(context(tmp_path, wda_enabled=False))).detail.startswith("off;")
    uncertified = await check_webdriveragent(context(tmp_path))
    assert uncertified.status == "ok" and "watched, not touched" in uncertified.detail
    ctx = context(tmp_path, keychain=PEM)
    unbuilt = await check_webdriveragent(ctx)
    assert unbuilt.status == "ok" and unbuilt.detail == "not set up yet for team TESTTEAM01"
    assert "Set up touch" in unbuilt.fix
    left_built(derived_for(tmp_path / "state" / "wda", "TESTTEAM01", XCODE))
    built = await check_webdriveragent(ctx)
    assert built.status == "ok" and built.detail.endswith("WebDriverAgentRunner_iphoneos26.5-arm64.xctestrun")
    named = await check_webdriveragent(context(tmp_path, real_devices_team_id="TESTTEAM01"))
    assert named.detail.startswith("set up for team TESTTEAM01"), "the setting stands in for a certificate here"

    async def several(run: Any) -> list[Team]:
        far = datetime(2126, 1, 1, tzinfo=timezone.utc)
        return [Team("TEAMAAAAA1", "One", far), Team("TEAMBBBBB2", "Two", far)]

    monkeypatch.setattr("sim_mirror.doctor.real_devices.development_teams", several)
    each = await check_webdriveragent(context(tmp_path))
    assert each.status == "ok" and each.detail.startswith("signed by each project's own team")


async def test_touch_now_says_whether_the_running_real_device_is_touched_and_how_to_get_it_back(
    tmp_path: Path,
) -> None:
    assert (await check_touch_now(context(tmp_path))).status == "skip"
    cable = "It cannot be touched or read yet: WebDriverAgent is reached only over the device's cable: plug it in."
    ended = "WebDriverAgent ended on the device (exit 1) (at 18:26:04)"

    def phone(touch: str, message: str | None, last_error: str | None = None, name: str = "Test iPhone") -> Any:
        return {"name": name, "touch": touch, "message": message, "last_error": last_error}

    answers: list[tuple[list[dict[str, Any]], str]] = [
        ([], "no daemon runs, so no device is in use"),
        ([phone("ready", None)], ""),
        ([phone("starting", "Starting WebDriverAgent on the device.")], ""),
        ([phone("off", None)], ""),
        ([phone("failed", cable)], ""),
        ([phone("failed", None, ended)], ""),
        ([phone("ready", None), phone("failed", cable, cable, name="iPad")], ""),
    ]
    found = []
    for answer in answers:
        ctx = context(tmp_path)
        ctx.touch_now = lambda answer=answer: answer  # type: ignore[misc]
        result = await check_touch_now(ctx)
        found.append((result.status, result.detail, result.fix))
    assert found == [
        ("ok", "no daemon runs, so no device is in use", ""),
        ("ok", "WebDriverAgent touches Test iPhone now", ""),
        ("ok", "Test iPhone: Starting WebDriverAgent on the device.", ""),
        ("ok", "Test iPhone: off", ""),
        ("warn", f"Test iPhone is not touched now: {cable}", TOUCH_FIX),
        ("warn", f"Test iPhone is not touched now: failed (last error: {ended})", TOUCH_FIX),
        ("warn", f"WebDriverAgent touches Test iPhone now; iPad is not touched now: {cable}", TOUCH_FIX),
    ], "every real device the daemon drives, whichever folder's it is"


async def test_the_camera_check_says_what_the_cable_screen_s_helper_last_said_in_its_latest_run(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    ctx = context(tmp_path)
    assert (await check_camera(ctx)).status == "skip", "no log folder to read"
    ctx.log_dir = logs
    nothing = await check_camera(ctx)
    assert (nothing.status, nothing.detail) == (
        "ok", "no cabled device's screen has been read yet; macOS asks for the Camera the first time",
    )  # fmt: skip
    # A simulator's helper's log is no capture run, whatever it says.
    (logs / "native-aaaa.log").write_text("[info] serving SIM on /s\n[error] the simulator went away\n")
    assert (await check_camera(ctx)).detail == nothing.detail
    camera = (
        "macOS has not let sim-mirror-helper use the Camera, which reading a cabled device's screen needs: allow it "
        "in System Settings > Privacy & Security > Camera"
    )
    phone = logs / "native-bbbb.log"
    # An earlier run that read the screen does not hide a later one refused the Camera.
    phone.write_text(
        f"[info] serving the screen of {PHONE_UDID} on /s\n[info] reading the screen of Test iPhone from capture x\n"
        f"[info] serving the screen of {PHONE_UDID} on /s\n[error] {camera}\n"
    )
    withheld = await check_camera(ctx)
    assert (withheld.status, withheld.detail, withheld.fix) == (
        "warn", f"the cable screen's helper said: {camera}", CAMERA_FIX,
    )  # fmt: skip
    phone.write_text(
        f"[info] serving the screen of {PHONE_UDID} on /s\n[error] {camera}\n"
        f"[info] serving the screen of {PHONE_UDID} on /s\n[info] reading the screen of Test iPhone from capture x\n"
    )
    allowed = await check_camera(ctx)
    read = "macOS lets sim-mirror-helper use the Camera: it read a cabled device"
    assert (allowed.status, allowed.detail) == ("ok", read)
    locked = "Test iPhone has sent no picture of its screen in 15 seconds: is it unlocked?"
    phone.write_text(f"[info] serving the screen of {PHONE_UDID} on /s\n[error] {locked}\n")
    failed = await check_camera(ctx)
    assert failed.status == "warn" and failed.detail == f"the cable screen's helper said: {locked} (its log is {phone})"
    phone.unlink()
    phone.mkdir()
    (logs / "native-cccc.log").symlink_to(logs / "gone.log")
    assert (await check_camera(ctx)).detail == nothing.detail, "a log that cannot be read says nothing"
    off = await check_camera(context(tmp_path, real_devices_screen="screenshot"))
    assert (off.status, off.detail) == ("ok", "not used: no cabled device's screen is read here")


async def test_recordings_need_a_folder_and_a_helper_that_renders(tmp_path: Path) -> None:
    ready = await check_recording(context(tmp_path, "render"))
    assert ready.status == "ok" and ready.detail.startswith(f"kept in {tmp_path / 'Movies'}, rendered by")
    unrendered = await check_recording(context(tmp_path))
    assert unrendered.status == "warn" and "cannot render recordings" in unrendered.detail
    (tmp_path / "file").write_text("not a folder")
    blocked = await check_recording(context(tmp_path, recording_folder=str(tmp_path / "file" / "Movies")))
    assert blocked.status == "fail" and "cannot be written" in blocked.detail
