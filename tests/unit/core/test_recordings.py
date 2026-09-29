# SPDX-License-Identifier: Apache-2.0
"""Recording a device: begun and stopped from a call, its touches noted as they land, kept as the settings say --
rendered by the helper, or a simulator's movie as recorded when it cannot be -- and the oldest let go."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.recordings import (
    RecordingOptions,
    RecordingRefused,
    Recordings,
    default_folder,
    recording_stem,
)
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakeControl, FakePhoneBackend
from sim_mirror.testing.recording import FakeRenderer, SimctlRecorder
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope

TP1 = scope("tp-1")
WHEN = datetime(2026, 9, 29, 1, 15, 30, tzinfo=timezone.utc)


def recordings_over(
    rig: DeviceRig, renderer: FakeRenderer, tmp_path: Path, *, tool: SimctlRecorder | None = None, now: list[float]
) -> Recordings:
    async def later(delay: float) -> None:
        await asyncio.sleep(3600)

    return Recordings(
        renderer,
        tool_recorder=tool,
        folder_for=lambda config: tmp_path / "recordings",
        clock=lambda: now[0],
        wall=lambda: WHEN,
        sleep=later,
    )


def options(config: SimConfig, **changes: Any) -> RecordingOptions:
    return RecordingOptions.from_config(config, **changes)


async def test_a_simulator_is_recorded_by_simctl_its_touches_noted_and_it_is_kept_as_asked(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    renderer, tool, now = FakeRenderer(), SimctlRecorder(), [10.0]
    recordings = recordings_over(rig, renderer, tmp_path, tool=tool, now=now)
    run = await recordings.start(
        instance, config, rig.manager.control(instance), by="Claude", options=options(config, format="both")
    )
    assert instance.recording is run and tool.started == [(instance.udid, "h264")]
    assert instance.describe(12.0)["recording"] == {"id": run.id, "since_ms": 2000, "by": "Claude", "max_ms": 300_000}
    now[0] = 11.0
    run.agent("tap", [(201, 437)], 0.08, 0.25)
    run.person("move", 1, 1)
    run.person("down", 100, 800)
    run.person("move", 100, 500)
    now[0] = 11.5
    run.person("up", 100, 200)
    run.person("down", 50, 50)
    run.person("up", 50, 50)
    with pytest.raises(RecordingRefused, match="already being recorded"):
        await recordings.start(instance, config, rig.manager.control(instance), by="x", options=options(config))
    kept = await recordings.stop(instance)
    assert instance.recording is None
    [job] = renderer.jobs
    assert job.input_kind == "movie" and job.mp4 is not None and job.gif is not None
    assert [(t.kind, t.by, t.points[0]) for t in job.touches] == [
        ("tap", "agent", (0.5, 0.5)),
        ("swipe", "person", (0.2488, 0.9153)),
        ("tap", "person", (0.1244, 0.0572)),
    ]
    assert job.touches[0].t == 1.25 and job.touches[1].t == 1.0 and job.touches[1].duration == 0.5
    local = WHEN.astimezone()
    assert kept["id"] == recording_stem(local, instance.name) == f"{local:%Y%m%d-%H%M%S}-simmirror-alpha-tp-1"
    assert kept["started_at"] == "2026-09-29T01:15:30Z" and kept["duration_ms"] == 2500 and kept["notes"] == []
    assert [(f["format"], f["bytes"], f["width"]) for f in kept["files"]] == [("mp4", 1500, 400), ("gif", 1500, 400)]
    folder = tmp_path / "recordings"
    assert json.loads((folder / f"{kept['id']}.json").read_text()) == kept
    assert not list(folder.glob(".*")), "the raw recording and its log are gone"
    assert recordings.listing(config) == [kept]
    assert recordings.file(config, f"{kept['id']}.gif") == folder / f"{kept['id']}.gif"
    for bad in ("../etc/passwd", f"{kept['id']}.json", "20260929-011530-x.mp4"):
        assert recordings.file(config, bad) is None
    with pytest.raises(RecordingRefused, match="is not being recorded"):
        await recordings.stop(instance)


async def test_a_movie_that_cannot_be_rendered_is_kept_as_recorded_and_says_why(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(rig, FakeRenderer(refuse="no helper"), tmp_path, tool=SimctlRecorder(), now=[0.0])
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    kept = await recordings.stop(instance)
    assert kept["files"][0]["format"] == "mp4" and kept["files"][0]["bytes"] == len(b"movie")
    assert kept["notes"] == ["kept as recorded, without its touches drawn or sped up: no helper"]
    gif = options(config, format="gif")
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=gif)
    with pytest.raises(RecordingRefused, match="no helper"):
        await recordings.stop(instance)


async def test_a_real_device_is_recorded_from_its_stream_which_needs_the_helper(tmp_path: Path) -> None:
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=VIEW_ONLY | {Capability.STREAM_H264})
    phone.engine.chunks = [b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce\x00\x00\x00\x01\x65\x88"]
    rig = DeviceRig(tmp_path, phones=FakePhoneBackend(), phone=phone)
    await rig.manager.choose(TP1, PHONE_UDID)
    instance = await rig.up()
    config = rig.config.get(TP1)
    missing = recordings_over(rig, FakeRenderer(missing="it is not built"), tmp_path, tool=SimctlRecorder(), now=[0.0])
    with pytest.raises(RecordingRefused, match="needs SimMirror's native helper: it is not built"):
        await missing.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    renderer = FakeRenderer()
    recordings = recordings_over(rig, renderer, tmp_path, tool=SimctlRecorder(), now=[0.0])
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    for _ in range(20):
        await asyncio.sleep(0)
    await recordings.stop(instance)
    assert renderer.jobs[0].input_kind == "frames"
    rig.phone.engine.chunks = []  # type: ignore[union-attr]
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    with pytest.raises(RecordingRefused, match="took no frames"):
        await recordings.stop(instance)


async def test_a_demo_status_bar_is_given_for_the_recording_and_taken_away_after(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(rig, FakeRenderer(), tmp_path, tool=SimctlRecorder(), now=[0.0])
    control = rig.manager.control(instance)
    await recordings.start(instance, config, control, by="Ann", options=options(config, status_bar=True))
    assert rig.argv()[-1][:4] == ("simctl", "status_bar", instance.udid, "override")
    await recordings.stop(instance)
    assert ("simctl", "status_bar", instance.udid, "clear") in rig.argv()
    rig.xcrun.on("simctl", "status_bar", rc=1, err="not supported")
    await recordings.start(instance, config, control, by="Ann", options=options(config, status_bar=True))
    assert instance.recording is not None and not isinstance(instance.recording, str)
    await recordings.stop(instance)
    rig.xcrun.on("simctl", "status_bar", out="")
    silent = Recordings(
        FakeRenderer(),
        tool_recorder=SimctlRecorder(says="refused\n"),
        folder_for=lambda c: tmp_path,
        start_timeout=0.05,
    )
    with pytest.raises(RecordingRefused, match="did not start: refused"):
        await silent.start(instance, config, control, by="Ann", options=options(config, status_bar=True))
    assert rig.argv()[-1] == ("simctl", "status_bar", instance.udid, "clear") and instance.recording is None


async def test_a_recording_stops_by_itself_at_its_limit_and_when_its_device_is_let_go(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    limits: list[float] = []

    async def at_once(delay: float) -> None:
        limits.append(delay)

    renderer = FakeRenderer()
    recordings = Recordings(
        renderer, tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path / "r", sleep=at_once
    )
    run = await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    assert run.watchdog is not None
    await run.watchdog
    assert limits == [300] and instance.recording is None and run.finishing is not None
    assert (await run.finishing)["device"] == instance.name
    again = await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    await rig.manager.stop(TP1)
    assert instance.recording is None and again.finishing is not None and (await again.finishing)["files"]
    await again.end()


async def test_a_recording_needs_a_screen_and_its_options_are_checked(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", hold=True))
    instance = await rig.manager.ensure(TP1)
    config = rig.config.get(TP1)
    recordings = recordings_over(rig, FakeRenderer(), tmp_path, tool=SimctlRecorder(), now=[0.0])
    with pytest.raises(RecordingRefused, match="is not showing its screen yet"):
        await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    with pytest.raises(RecordingRefused, match="format is one of mp4, gif, both"):
        options(config, format="webm")
    with pytest.raises(RecordingRefused, match=r"speed is one of 1, 1\.5, 2, 4"):
        options(config, speed="3")
    chosen = options(config, format="gif", touches=False, speed="2", status_bar=False)
    assert (chosen.format, chosen.touches, chosen.speed, chosen.status_bar) == ("gif", False, 2.0, False)
    assert rig.idb.release is not None
    rig.idb.release.set()


async def test_the_oldest_recordings_go_beyond_the_ones_kept_and_a_stray_note_is_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "recordings"
    folder.mkdir()
    recordings = Recordings(FakeRenderer(), folder_for=lambda config: folder)
    for day in range(1, 4):
        stem = f"2026092{day}-000000-phone"
        (folder / f"{stem}.mp4").write_bytes(b"m")
        note = {"id": stem, "started_at": f"2026-09-2{day}T00:00:00Z", "files": [{"name": f"{stem}.mp4"}]}
        (folder / f"{stem}.json").write_text(json.dumps(note))
    (folder / "notes.json").write_text("[]")
    (folder / "broken.json").write_text("{")
    (folder / "odd.json").write_text(json.dumps({"id": "x", "started_at": "z", "files": [{"name": "../x"}]}))
    config = SimConfig.defaults().with_values(recording_keep=2)
    assert recordings.prune(config) == ["20260921-000000-phone"]
    assert sorted(path.name for path in folder.glob("2026*")) == [
        "20260922-000000-phone.json",
        "20260922-000000-phone.mp4",
        "20260923-000000-phone.json",
        "20260923-000000-phone.mp4",
    ]
    assert Recordings(FakeRenderer(), folder_for=lambda config: tmp_path / "none").listing(config) == []


def test_recordings_are_kept_in_the_movies_folder_unless_the_setting_names_one() -> None:
    assert default_folder(SimConfig.defaults()) == Path.home() / "Movies" / "SimMirror"
    assert default_folder(SimConfig.defaults().with_values(recording_folder="/tmp/demos")) == Path("/tmp/demos")


async def test_what_a_recording_does_not_do_it_leaves_alone(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    control = FakeControl()
    held: list[float] = []

    async def hold(delay: float) -> None:
        held.append(delay)

    recordings = Recordings(
        FakeRenderer(), tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path / "r", sleep=hold
    )
    instance.demo_status_bar = True
    run = await recordings.start(instance, config, control, by="Ann", options=options(config, status_bar=True))
    assert not run.status_bar and control.calls == [], "the device already has its demo status bar"
    screen, instance.screen = instance.screen, None
    run.person("down", 10, 10)
    run.person("up", 10, 10)
    instance.screen = screen
    assert run.touches[0].points == ((0.0, 0.0),)
    instance.recording = None
    assert run.watchdog is not None
    await run.watchdog
    assert run.finishing is None, "its limit passed after it had been let go of"
    instance.demo_status_bar = False
    await run.end()
    assert run.finishing is not None
    await run.finishing
    run = await recordings.start(instance, config, control, by="Ann", options=options(config, status_bar=True))
    control.fail = DeviceControlError("the device went away")
    with caplog.at_level(logging.WARNING):
        await recordings.stop(instance)
    assert "could not give" in caplog.text and "its own status bar back: the device went away" in caplog.text
    control.fail = None
    quiet = Recordings(
        FakeRenderer(),
        tool_recorder=SimctlRecorder(says="nothing\n"),
        folder_for=lambda c: tmp_path,
        start_timeout=0.01,
    )
    control.calls.clear()
    with pytest.raises(RecordingRefused, match="did not start"):
        await quiet.start(instance, config, control, by="Ann", options=options(config, status_bar=False))
    assert control.calls == []
