# SPDX-License-Identifier: Apache-2.0
"""Recording as smooth as the screen sends it: kept at its frame rate, of the part of the screen asked, the stream
boosted while it runs, how fast the screen's pictures came said -- and warned of when it stutters -- and an Android
device kept quiet while it is recorded."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.recordings import (
    PRESETS,
    CaptureRate,
    RecordingOptions,
    RecordingRefused,
    Recordings,
    RenderJob,
)
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakePhoneBackend
from sim_mirror.testing.recording import FakeRenderer, SimctlRecorder
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope

TP1 = scope("tp-1")
WHEN = datetime(2026, 10, 31, 9, 41, 0, tzinfo=timezone.utc)
STREAM = b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce\x00\x00\x00\x01\x65\x88"


def recordings_over(renderer: FakeRenderer, folder: Path, now: list[float] | None = None, **kwargs: Any) -> Recordings:
    async def later(delay: float) -> None:
        await asyncio.sleep(3600)

    clock = now if now is not None else [0.0]
    return Recordings(
        renderer,
        tool_recorder=SimctlRecorder(),
        folder_for=lambda config: folder,
        clock=lambda: clock[0],
        wall=lambda: WHEN,
        sleep=later,
        **kwargs,
    )


def options(config: SimConfig, **changes: Any) -> RecordingOptions:
    return RecordingOptions.from_config(config, **changes)


async def phone_rig(tmp_path: Path, *, live: bool = True, fps_limit: int | None = None) -> DeviceRig:
    capabilities = VIEW_ONLY | ({Capability.STREAM_H264} if live else set())
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=capabilities, fps_limit=fps_limit)
    phone.engine.chunks = [STREAM]
    rig = DeviceRig(tmp_path, phones=FakePhoneBackend(), phone=phone)
    await rig.manager.choose(TP1, PHONE_UDID)
    return rig


async def frames_for(tasks: int = 20) -> None:
    for _ in range(tasks):
        await asyncio.sleep(0)


class QuietControl:
    """A device's own tool that can turn its notifications off, as an Android device's can."""

    def __init__(self, *, was: str | None = "off", refuse: str | None = None, refuse_back: bool = False) -> None:
        self.was = was
        self.refuse = refuse
        self.refuse_back = refuse_back
        self.said: list[tuple[str, ...]] = []

    async def demo_status_bar(self, udid: str) -> None:
        self.said.append(("demo",))

    async def clear_status_bar(self, udid: str) -> None:
        self.said.append(("clear",))

    async def quiet(self, udid: str) -> str | None:
        if self.refuse:
            raise DeviceControlError(self.refuse)
        self.said.append(("quiet",))
        return self.was

    async def unquiet(self, udid: str, was: str) -> None:
        self.said.append(("unquiet", was))
        if self.refuse_back:
            raise DeviceControlError("adb went away")


def test_a_recording_is_kept_at_its_frame_rate_which_a_call_may_change() -> None:
    config = SimConfig.defaults()
    assert options(config).fps == 30
    assert options(config.with_values(recording_fps="120")).fps == 120
    assert options(config, fps=60, preset="pr").fps == 60
    for wrong in (45, True, "60"):
        with pytest.raises(RecordingRefused, match="fps is one of 30, 60, 120"):
            options(config, fps=wrong)
    crop = (0.1, 0.2, 0.5, 0.25)
    asked = options(config, fps=60, crop=crop, also="pr")
    copy = asked.as_preset("pr")
    assert (copy.fps, copy.crop, copy.mp4_width) == (60, crop, PRESETS["pr"]["width"])


def test_a_job_asks_the_helper_for_its_rate_and_its_part_of_the_picture(tmp_path: Path) -> None:
    job = RenderJob(tmp_path / "a.raw", "frames", (), tmp_path / "a.mp4", None, "h264", 1.0, 12, 600)
    assert job.to_json()["mp4_fps"] == 30 and "crop" not in job.to_json()
    cut = RenderJob(
        tmp_path / "a.raw", "frames", (), tmp_path / "a.mp4", None, "h264", 1.0, 12, 600, mp4_fps=120,
        crop=(0.1, 0.2, 0.5, 0.25),
    )  # fmt: skip
    assert cut.to_json()["mp4_fps"] == 120
    assert cut.to_json()["crop"] == {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.25}


def test_a_stream_is_as_wide_as_the_widest_file_needs_and_no_wider_than_the_screen() -> None:
    config = SimConfig.defaults()
    assert options(config).widest(1206) == 1206, "an MP4 kept at the screen's own width"
    assert options(config, width=450).widest(1206) == 450
    assert options(config, format="gif").widest(1206) == 600
    assert options(config, format="gif", also="pr").widest(1206) == 600
    assert options(config, width=450, crop=(0.0, 0.0, 0.5, 0.5)).widest(1206) == 900, "a region needs more of it"
    assert options(config, width=900, crop=(0.0, 0.0, 0.25, 0.5)).widest(1206) == 1206


async def test_a_real_device_is_streamed_at_the_recordings_rate_and_its_own_width_while_it_is_recorded(
    tmp_path: Path,
) -> None:
    rig = await phone_rig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    assert instance.hub is not None and instance.screen is not None
    before = instance.hub.settings
    renderer = FakeRenderer(source=CaptureRate(pictures=120, busiest_fps=58, longest_gap_s=0.05))
    recordings = recordings_over(renderer, tmp_path / "r")
    run = await recordings.start(
        instance, config, rig.manager.control(instance), by="Ann", options=options(config, fps=60)
    )
    assert run.warnings == ()
    assert instance.hub.settings.fps == 60 and instance.hub.settings.max_width == instance.screen.width_px
    await frames_for()
    kept = (await recordings.stop(instance))["recording"]
    assert instance.hub.settings == before
    assert renderer.jobs[0].mp4_fps == 60
    assert kept["capture"] == {"fps": 60, "pictures": 120, "busiest_fps": 58, "longest_gap_ms": 50}
    assert not any("stutters" in note for note in kept["notes"])
    listed = recordings.listing(config)
    assert listed[0]["capture"] == kept["capture"]


async def test_a_screen_shown_by_screenshot_is_recorded_no_faster_than_it_can_be_and_says_it_stutters(
    tmp_path: Path,
) -> None:
    rig = await phone_rig(tmp_path, live=False, fps_limit=3)
    instance = await rig.up()
    config = rig.config.get(TP1)
    assert instance.hub is not None
    base = instance.hub.settings
    renderer = FakeRenderer(source=CaptureRate(pictures=9, busiest_fps=3, longest_gap_s=0.4))
    now = [0.0]
    recordings = recordings_over(renderer, tmp_path / "r", now)
    run = await recordings.start(
        instance, config, rig.manager.control(instance), by="Ann", options=options(config, fps=60)
    )
    assert instance.hub.settings.fps == 3 and instance.hub.settings.max_width == base.max_width
    [warning] = run.warnings
    assert "shown by screenshot, at most 3 a second, not streamed live" in warning
    assert warning.endswith("sim_device restart brings its live screen back")
    await frames_for()
    now[0] = 3.0
    kept = (await recordings.stop(instance))["recording"]
    assert kept["notes"][0] == warning
    assert kept["notes"][1].startswith("the screen sent at most 3 pictures a second while it changed, fewer than")
    assert "(the longest wait was 0.40s)" in kept["notes"][1]
    # A recording too short to have a busiest second is not judged by one.
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config, fps=60))
    await frames_for()
    now[0] = 4.5
    kept = (await recordings.stop(instance))["recording"]
    assert not any("pictures a second while it changed" in note for note in kept["notes"])


async def test_a_screen_that_stood_still_or_a_helper_that_did_not_count_says_nothing_of_stutter(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    now = [0.0]
    # Its status bar given a demo one: a picture before, and one after.
    still = recordings_over(FakeRenderer(source=CaptureRate(2, 2, 0.12)), tmp_path / "s", now)
    await still.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    now[0] = 13.0
    kept = (await still.stop(instance))["recording"]
    assert kept["capture"] == {"fps": 30, "pictures": 2, "busiest_fps": 2, "longest_gap_ms": 120}
    assert kept["notes"] == []
    older = recordings_over(FakeRenderer(), tmp_path / "o")
    await older.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    assert (await older.stop(instance))["recording"]["capture"] is None
    # A note kept before pictures were counted reads as one with none.
    note = json.loads((tmp_path / "o" / f"{older.listing(config)[0]['id']}.json").read_text())
    del note["capture"]
    (tmp_path / "o" / f"{note['id']}.json").write_text(json.dumps(note))
    assert older.listing(config)[0]["capture"] is None


async def test_an_android_device_is_kept_quiet_while_it_is_recorded_and_given_its_own_mode_back(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1).with_values(recording_do_not_disturb=True)
    control = QuietControl(was="priority")
    recordings = recordings_over(FakeRenderer(), tmp_path / "r")
    run = await recordings.start(instance, config, control, by="Ann", options=options(config, status_bar=False))
    assert run.warnings == () and control.said == [("quiet",)]
    await recordings.stop(instance)
    assert control.said == [("quiet",), ("unquiet", "priority")]
    # Already silent: nothing to give back.
    silent = QuietControl(was=None)
    await recordings.start(instance, config, silent, by="Ann", options=options(config, status_bar=False))
    await recordings.stop(instance)
    assert silent.said == [("quiet",)]
    # Not asked: never touched.
    untouched = QuietControl()
    await recordings.start(
        instance, rig.config.get(TP1), untouched, by="Ann", options=options(config, status_bar=False)
    )
    await recordings.stop(instance)
    assert untouched.said == []


async def test_a_device_that_cannot_be_kept_quiet_is_recorded_and_said_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1).with_values(recording_do_not_disturb=True)
    recordings = recordings_over(FakeRenderer(), tmp_path / "r")
    refused = QuietControl(refuse="cmd: notification: not found")
    run = await recordings.start(instance, config, refused, by="Ann", options=options(config, status_bar=False))
    assert run.warnings == ("Do Not Disturb could not be turned on, so notifications may show: cmd: notification: "
                            "not found",)  # fmt: skip
    kept = (await recordings.stop(instance))["recording"]
    assert kept["notes"][0] == run.warnings[0]
    # A simulator has no other app's notifications to keep out: nothing is said.
    run = await recordings.start(
        instance, config, rig.manager.control(instance), by="Ann", options=options(config, status_bar=False)
    )
    assert run.warnings == ()
    await recordings.stop(instance)
    # Giving the mode back fails: logged, and the recording is kept.
    stuck = QuietControl(refuse_back=True)
    await recordings.start(instance, config, stuck, by="Ann", options=options(config, status_bar=False))
    assert (await recordings.stop(instance))["recording"]["files"]
    assert "could not give" in caplog.text and "Do Not Disturb mode back: adb went away" in caplog.text


async def test_a_real_iphone_asked_to_be_quiet_is_told_to_turn_on_a_focus(tmp_path: Path) -> None:
    rig = await phone_rig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1).with_values(recording_do_not_disturb=True)
    recordings = recordings_over(FakeRenderer(), tmp_path / "r")
    run = await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    assert run.warnings == (
        f"{instance.name} cannot be put in Do Not Disturb from here: turn on a Focus on it, so other apps' "
        "notifications stay out of the recording",
    )
    await frames_for()
    await recordings.stop(instance)


async def test_a_recording_whose_stream_cannot_start_gives_everything_back(tmp_path: Path) -> None:
    rig = await phone_rig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1).with_values(recording_do_not_disturb=True)
    assert instance.hub is not None
    before = instance.hub.settings
    rig.phone.engine.chunks = []  # type: ignore[union-attr]
    control = QuietControl()
    recordings = recordings_over(FakeRenderer(), tmp_path / "r")

    async def broken() -> float:
        raise DeviceControlError("the screen went away")

    original = Recordings._source

    def failing(self: Recordings, *args: Any) -> Any:
        source = original(self, *args)
        source.start = broken  # type: ignore[method-assign]
        return source

    Recordings._source = failing  # type: ignore[method-assign]
    try:
        with pytest.raises(RecordingRefused, match="the screen went away"):
            await recordings.start(instance, config, control, by="Ann", options=options(config, fps=60))
    finally:
        Recordings._source = original  # type: ignore[method-assign]
    assert instance.hub.settings == before
    assert control.said == [("demo",), ("quiet",), ("unquiet", "off"), ("clear",)]
