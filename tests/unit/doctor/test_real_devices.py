# SPDX-License-Identifier: Apache-2.0
"""The doctor on real devices and recording: what is set up, and what a person does when it is not."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.iphone.wda import derived_for
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.real_devices import (
    check_cable_screen,
    check_real_devices,
    check_recording,
    check_webdriveragent,
)
from sim_mirror.testing.fakes import FakeXcrun, fixture_json

XCODE = "/Applications/Xcode27.app/Contents/Developer"


def context(tmp_path: Path, *features: str, xcrun: FakeXcrun | None = None, **settings: Any) -> DoctorContext:
    helper = tmp_path / "sim-mirror-helper"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    said = json.dumps({"version": __version__, "wire": 1, "core_simulator": None, "features": list(features)})

    async def run(argv: Sequence[str]) -> tuple[int, str]:
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


async def test_webdriveragent_is_checked_for_its_team_and_its_build(tmp_path: Path) -> None:
    assert (await check_webdriveragent(context(tmp_path))).detail.startswith("off;")
    unsigned = await check_webdriveragent(context(tmp_path, wda_enabled=True))
    assert unsigned.status == "warn" and "sim-mirror wda teams" in unsigned.fix
    ctx = context(tmp_path, wda_enabled=True, real_devices_team_id="TESTTEAM01")
    unbuilt = await check_webdriveragent(ctx)
    assert unbuilt.status == "warn" and "wda setup --device" in unbuilt.fix
    products = derived_for(tmp_path / "state" / "wda", "TESTTEAM01", XCODE) / "Build" / "Products"
    products.mkdir(parents=True)
    (products / "WebDriverAgentRunner_iphoneos27.0-arm64.xctestrun").write_text("<plist/>")
    built = await check_webdriveragent(ctx)
    assert built.status == "ok" and built.detail.endswith("WebDriverAgentRunner_iphoneos27.0-arm64.xctestrun")


async def test_recordings_need_a_folder_and_a_helper_that_renders(tmp_path: Path) -> None:
    ready = await check_recording(context(tmp_path, "render"))
    assert ready.status == "ok" and ready.detail.startswith(f"kept in {tmp_path / 'Movies'}, rendered by")
    unrendered = await check_recording(context(tmp_path))
    assert unrendered.status == "warn" and "cannot render recordings" in unrendered.detail
    (tmp_path / "file").write_text("not a folder")
    blocked = await check_recording(context(tmp_path, recording_folder=str(tmp_path / "file" / "Movies")))
    assert blocked.status == "fail" and "cannot be written" in blocked.detail
