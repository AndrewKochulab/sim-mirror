# SPDX-License-Identifier: Apache-2.0
"""A cabled device's live screen: read by the native helper's capture mode, found by its name, remembered after."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.connectors.iphone.capture import HELLO_SLACK_S, CableCapture
from sim_mirror.connectors.native.helper import HelperLauncher, HelperVersion
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun
from sim_mirror.testing.native import FakeHelper, FakeHelperSpawn, short_run_dir

DISPLAY = Display(1179, 2556, 3.0, "portrait")
CONFIG = SimConfig.defaults().with_values(real_devices_capture_timeout=5)


def says(*features: str) -> Any:
    async def ask(binary: str) -> HelperVersion | None:
        return HelperVersion(__version__, 1, None, features)

    return ask


class Shots:
    """devicectl's screenshots, as the reference that tells apart devices sharing a name."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.taken: list[Path] = []

    async def screenshot(self, udid: str, destination: Path) -> None:
        self.taken.append(destination)
        if self.fail:
            raise DevicectlError("the device is locked")
        destination.write_bytes(b"\x89PNG")


@contextmanager
def capture(
    tmp_path: Path, helper: FakeHelper | None = None, *features: str
) -> Iterator[tuple[CableCapture, FakeHelperSpawn]]:
    spawn = FakeHelperSpawn(helper or FakeHelper(hid=None, source="C1"))
    binary = tmp_path / "sim-mirror-helper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with short_run_dir() as run:
        launcher = HelperLauncher(
            run_dir=run,
            log_dir=tmp_path,
            owner_tag="SimMirrorTest",
            spawn=spawn,
            signal_group=spawn.signal_group,
            pid_alive=lambda pid: False,
            owner=7,
        )
        yield CableCapture(launcher, candidates=lambda: (binary,), ask_version=says(*(features or ("capture",)))), spawn


async def phone() -> Any:
    devices = await Devicectl(FakeXcrun().with_devicectl()).devices()
    return next(device for device in devices if device.udid == PHONE_UDID)


def flag(argv: tuple[str, ...], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


async def test_a_cabled_devices_screen_is_served_by_the_helper_and_its_capture_device_remembered(
    tmp_path: Path,
) -> None:
    device = await phone()
    with capture(tmp_path) as (cable, spawn):
        live = await cable.open(device, DISPLAY, CONFIG, Devicectl(FakeXcrun()), twins=False)
        try:
            argv = spawn.started[0][0]
            assert argv[1] == "capture" and flag(argv, "--name") == "Test iPhone" and flag(argv, "--wait") == "5"
            assert flag(argv, "--capture-id") is None and flag(argv, "--reference") is None
            assert live.alive() and (await live.screen.describe()).width_px == 1206
        finally:
            await live.close()
        assert not live.alive()
        again = await cable.open(device, DISPLAY, CONFIG, Devicectl(FakeXcrun()), twins=True)
        await again.close()
        assert flag(spawn.started[1][0], "--capture-id") == "C1", "found before, so no reference is taken"
        assert flag(spawn.started[1][0], "--reference") is None
        assert await cable.reap_orphans() == 0
    assert HELLO_SLACK_S > 0


async def test_a_device_sharing_its_name_is_told_apart_by_a_screenshot_taken_first(tmp_path: Path) -> None:
    device = await phone()
    shots = Shots()
    with capture(tmp_path, FakeHelper(hid=None)) as (cable, spawn):
        live = await cable.open(device, DISPLAY, CONFIG, shots, twins=True)  # type: ignore[arg-type]
        await live.close()
        reference = flag(spawn.started[0][0], "--reference")
        assert reference == str(shots.taken[0]) and reference.endswith(".reference.png")
        assert not Path(reference).exists(), "the reference goes once the helper has used it"
        failing = Shots(fail=True)
        live = await cable.open(device, DISPLAY, CONFIG, failing, twins=True)  # type: ignore[arg-type]
        await live.close()
        assert failing.taken and flag(spawn.started[1][0], "--reference") is None


async def test_a_helper_that_cannot_capture_or_read_the_screen_is_refused_with_why(tmp_path: Path) -> None:
    device = await phone()
    refused = pytest.raises(ConnectorUnavailable, match="cannot show a cabled device's screen; build it again")
    with capture(tmp_path, None, "render") as (cable, _spawn), refused:
        await cable.open(device, DISPLAY, CONFIG, Devicectl(FakeXcrun()), twins=False)
    denied = FakeHelper(hid=None, hello_failure="macOS has not let sim-mirror-helper use the Camera")
    with capture(tmp_path, denied) as (cable, spawn):
        with pytest.raises(ConnectorUnavailable, match="use the Camera") as refused:
            await cable.open(device, DISPLAY, CONFIG, Devicectl(FakeXcrun()), twins=False)
        assert refused.value.status == 409 and spawn.processes[0].returncode is not None


class Silent(FakeHelper):
    """A capture helper still asking for the Camera: it answers everything but its hello."""

    async def _answer(self, request_id: int, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        if request.get("op") == "hello":
            await asyncio.Event().wait()
        await super()._answer(request_id, request, writer)


async def test_a_capture_let_go_of_while_it_starts_ends_its_helper(tmp_path: Path) -> None:
    device = await phone()
    with capture(tmp_path, Silent(hid=None)) as (cable, spawn):
        opening = asyncio.ensure_future(cable.open(device, DISPLAY, CONFIG, Devicectl(FakeXcrun()), twins=False))
        while not spawn.helper.requests or spawn.helper.requests[-1].get("op") != "hello":
            await asyncio.sleep(0.01)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert spawn.processes[0].returncode is not None
