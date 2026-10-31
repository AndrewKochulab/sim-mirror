# SPDX-License-Identifier: Apache-2.0
"""A recording is as long as it ran, takes its name only once it is whole, and is never lost: one that stopped by itself
is still answered by stop, one still rendering is said to be and where it will be kept, and one that cannot be
rendered keeps its raw recording. A call may say how long it runs and what its file is called."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.config.schema import SETTINGS, Whole
from sim_mirror.connectors.base import Capability
from sim_mirror.core.recordings import (
    FILE_NAME,
    MAX_SECONDS,
    RecordingOptions,
    RecordingRefused,
    Recordings,
    Rendered,
    RenderJob,
    recording_stem,
)
from sim_mirror.testing.fakes import PHONE_UDID, FakeConnector, FakePhoneBackend
from sim_mirror.testing.recording import FakeRenderer, SimctlRecorder
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope

TP1 = scope("tp-1")
WHEN = datetime(2026, 9, 29, 1, 15, 30, tzinfo=timezone.utc)


def recordings_over(renderer: FakeRenderer, folder: Path, now: list[float], **kwargs: Any) -> Recordings:
    async def later(delay: float) -> None:
        await asyncio.sleep(3600)

    return Recordings(
        renderer,
        folder_for=lambda config: folder,
        clock=lambda: now[0],
        wall=lambda: WHEN,
        **{"sleep": later, "tool_recorder": SimctlRecorder(), **kwargs},
    )


async def at_once(delay: float) -> None:
    return None


def options(config: SimConfig, **changes: Any) -> RecordingOptions:
    return RecordingOptions.from_config(config, **changes)


class Watching(FakeRenderer):
    """Renders as `FakeRenderer` does, noting what the folder held while it did -- and, when told, holding each render
    until `go` is set."""

    def __init__(self, *, hold: bool = False) -> None:
        super().__init__()
        self.go = asyncio.Event()
        if not hold:
            self.go.set()
        self.seen: list[list[str]] = []

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        made = await super().render(job, config)
        self.seen.append(sorted(path.name for path in job.input.parent.iterdir()))
        await self.go.wait()
        return made


class HalfWrites(FakeRenderer):
    """Writes the files a job names, then fails as a helper whose render broke off does."""

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        await super().render(job, config)
        raise RecordingRefused("the recording could not be rendered: the disk is full")


class Gone(FakeRenderer):
    """Loses the recording it was given, and says it cannot render it."""

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        job.input.unlink()
        raise RecordingRefused("no helper")


class Answers(FakeRenderer):
    """Writes nothing it is asked for, and answers what a test says."""

    def __init__(self, files: list[Rendered]) -> None:
        super().__init__()
        self.files = files

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        self.jobs.append(job)
        return self.files


async def test_a_recording_is_rendered_as_long_as_it_ran_however_still_its_screen(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    renderer, now = FakeRenderer(), [10.0]
    recordings = recordings_over(renderer, tmp_path / "r", now)
    control = rig.manager.control(instance)
    await recordings.start(instance, config, control, by="Claude", options=options(config, also="pr"))
    now[0] = 130.0
    answer = await recordings.stop(instance)
    whole, copy = renderer.jobs
    assert whole.duration_s == copy.duration_s == 120.0, "the clock's two minutes, not the last frame's time"
    assert whole.to_json()["duration_s"] == 120.0
    assert answer["recorded_ms"] == 120_000 and not answer["stopped_by_itself"] and not answer["rendering"]


async def test_a_file_has_its_name_only_once_it_is_finished_and_its_note_is_written_whole(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    renderer, folder = Watching(), tmp_path / "r"
    recordings = recordings_over(renderer, folder, [0.0])
    control = rig.manager.control(instance)
    run = await recordings.start(instance, config, control, by="Claude", options=options(config, format="both"))
    kept = (await recordings.stop(instance))["recording"]
    [job] = renderer.jobs
    assert job.mp4 is not None and job.gif is not None
    assert (job.mp4.name, job.gif.name) == (f".{run.id}.partial.mp4", f".{run.id}.partial.gif")
    assert recordings.file(config, job.mp4.name) is None, "nothing serves a file half written"
    [while_rendering] = renderer.seen
    assert f"{run.id}.mp4" not in while_rendering and f"{run.id}.gif" not in while_rendering
    assert [file["path"] for file in kept["files"]] == [str(folder / f"{run.id}.mp4"), str(folder / f"{run.id}.gif")]
    assert sorted(path.name for path in folder.iterdir()) == [f"{run.id}.gif", f"{run.id}.json", f"{run.id}.mp4"]
    assert (folder / f"{run.id}.json").stat().st_mode & 0o777 == 0o600


async def test_a_failed_render_leaves_nothing_half_written_and_keeps_the_raw_frames(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=VIEW_ONLY | {Capability.STREAM_H264})
    phone.engine.chunks = [b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce\x00\x00\x00\x01\x65\x88"]
    rig = DeviceRig(tmp_path, phones=FakePhoneBackend(), phone=phone)
    await rig.manager.choose(TP1, PHONE_UDID)
    instance = await rig.up()
    config = rig.config.get(TP1).with_values(recording_keep=1)
    now, folder = [0.0], tmp_path / "r"
    recordings = recordings_over(HalfWrites(), folder, now)
    control = rig.manager.control(instance)
    run = await recordings.start(instance, config, control, by="Ann", options=options(config, format="both"))
    for _ in range(20):
        await asyncio.sleep(0)
    now[0] = 42.0
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RecordingRefused) as refused:
            await recordings.stop(instance)
        await asyncio.sleep(0)
    raw = folder / f"{run.id}.raw"
    said = f"the recording could not be rendered: the disk is full; the raw recording is kept at {raw}"
    assert str(refused.value) == said
    assert raw.read_bytes().startswith(b"SMRF\x01") and raw.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in folder.iterdir()) == [f"{run.id}.json", f"{run.id}.raw"], "no partial left"
    [lost] = recordings.listing(config)
    assert (lost["files"], lost["duration_ms"], lost["notes"]) == ([], 42_000, [f"it could not be rendered: {said}"])
    assert f"recording {run.id} on {instance.udid} could not be kept" in caplog.text
    # It goes with its note when it is older than the recordings kept.
    newer = {**lost, "id": "20990101-000000-newer", "started_at": "2099-01-01T00:00:00Z"}
    (folder / "20990101-000000-newer.json").write_text(json.dumps(newer))
    assert recordings.prune(config) == [run.id] and not raw.exists()


async def test_a_raw_recording_that_cannot_be_kept_either_is_said(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(Gone(), tmp_path / "r", [0.0])
    gif = options(config, format="gif")
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=gif)
    with pytest.raises(RecordingRefused, match="no helper; its raw recording could not be kept either: "):
        await recordings.stop(instance)


async def test_a_render_that_answers_no_file_is_as_long_as_it_ran_and_another_file_stays_where_it_is(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    now = [0.0]
    recordings = recordings_over(Answers([]), tmp_path / "r", now)
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    now[0] = 7.5
    kept = (await recordings.stop(instance))["recording"]
    assert (kept["files"], kept["duration_ms"]) == ([], 7500), "nothing to measure, and no crash for it"
    elsewhere = tmp_path / "elsewhere.mp4"
    elsewhere.write_bytes(b"m")
    odd = recordings_over(Answers([Rendered(elsewhere, "mp4", 2, 2, 1.0)]), tmp_path / "r", now)
    await odd.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    assert (await odd.stop(instance))["recording"]["files"][0]["path"] == str(elsewhere)


async def test_stop_after_a_recording_stopped_by_itself_answers_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(FakeRenderer(), tmp_path / "r", [0.0], sleep=at_once)
    control = rig.manager.control(instance)
    run = await recordings.start(instance, config, control, by="Ann", options=options(config, max_seconds=60))
    assert run.state(0)["max_ms"] == 60_000 and run.watchdog is not None
    await run.watchdog
    assert instance.recording is None and recordings.ended(instance) is run and run.by_itself
    answer = await recordings.stop(instance, also="pr")
    assert answer["stopped_by_itself"] and not answer["rendering"]
    assert answer["recording"]["notes"] == [
        "it stopped by itself after 60s, its limit",
        "no pr copy: the recording had stopped by itself before one was asked for",
    ]
    assert recordings.listing(config)[0]["notes"] == ["it stopped by itself after 60s, its limit"], "kept so"
    assert recordings.ended(instance) is None
    with pytest.raises(RecordingRefused, match="is not being recorded"):
        await recordings.stop(instance)
    # A recording begun after one that stopped by itself is the one stop answers.
    first = await recordings.start(instance, config, control, by="Ann", options=options(config))
    assert first.watchdog is not None
    await first.watchdog
    second = await recordings.start(instance, config, control, by="Ann", options=options(config))
    assert recordings.ended(instance) is None
    assert (await recordings.stop(instance))["recording"]["id"] == second.id
    # One that stopped by itself and could not be kept says both.
    failing = recordings_over(FakeRenderer(refuse="no helper"), tmp_path / "f", [0.0], sleep=at_once)
    lost = await failing.start(instance, config, control, by="Ann", options=options(config, format="gif"))
    assert lost.watchdog is not None
    await lost.watchdog
    with pytest.raises(RecordingRefused, match=r"^it stopped by itself at 0s, its limit; no helper; the raw recording"):
        await failing.stop(instance)


async def test_stop_waits_a_while_for_rendering_then_says_where_it_will_be_kept(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    renderer, now, folder = Watching(hold=True), [0.0], tmp_path / "r"
    recordings = recordings_over(renderer, folder, now, stop_wait=0.01)
    control = rig.manager.control(instance)
    asked = options(config, format="both", speed="2")
    run = await recordings.start(instance, config, control, by="Ann", options=asked)
    now[0] = 2400.0
    answer = await recordings.stop(instance)
    mp4, gif = folder / f"{run.id}.mp4", folder / f"{run.id}.gif"
    assert answer["rendering"] and answer["recorded_ms"] == 2_400_000
    assert answer["recording"] == {
        "id": run.id,
        "device": instance.name,
        "started_at": "2026-09-29T01:15:30Z",
        "duration_ms": 1_200_000,
        "files": [],
        "notes": [
            f"still being rendered; it will be kept at {mp4} and {gif}, and listed with the recordings once it is"
        ],
        "capture": None,
    }
    assert recordings.finishing(config) == [
        {
            "id": run.id,
            "device": instance.name,
            "started_at": "2026-09-29T01:15:30Z",
            "recorded_ms": 2_400_000,
            "stopped_by_itself": False,
            "paths": [str(mp4), str(gif)],
        }
    ]
    assert recordings.listing(config) == [], "not listed as kept until it is"
    renderer.go.set()
    assert run.finishing is not None
    kept = await run.finishing
    assert recordings.finishing(config) == [] and recordings.listing(config) == [kept]
    assert mp4.is_file() and gif.is_file()


async def test_a_copy_asked_for_and_still_being_rendered_is_said_to_be(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    renderer, now = Watching(hold=True), [0.0]
    recordings = recordings_over(renderer, tmp_path / "r", now, stop_wait=0.01)
    run = await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    now[0] = 30.0
    answer = await recordings.stop(instance, also="pr")
    # A viewer that asked for a pr copy and hears nothing of one says this server does not make one.
    assert answer["rendering"] and answer["recording"]["notes"][1] == "its pr copy is being rendered with it"
    renderer.go.set()
    assert run.finishing is not None
    await run.finishing


async def test_a_recording_whose_keeping_is_called_off_is_no_longer_finishing(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(Watching(hold=True), tmp_path / "r", [0.0], stop_wait=0.01)
    run = await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=options(config))
    assert (await recordings.stop(instance))["rendering"]
    assert run.finishing is not None
    run.finishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.finishing
    assert recordings.finishing(config) == []


def test_a_call_may_say_how_long_a_recording_runs_and_what_its_file_is_called() -> None:
    config = SimConfig.defaults()
    assert options(config).max_seconds == 300 and options(config, max_seconds=3600).max_seconds == 3600
    for wrong in (4, 3601, True, "60", 60.0):
        with pytest.raises(RecordingRefused, match="max_seconds is a whole number of seconds from 5 to 3600"):
            options(config, max_seconds=wrong)
    [setting] = [setting for setting in SETTINGS if setting.path == "recording.max_seconds"]
    assert isinstance(setting.rule, Whole) and (setting.rule.low, setting.rule.high) == MAX_SECONDS
    assert options(config).label == "" and options(config, label="Lock screen: 60 s!").label == "lock-screen-60-s"
    assert options(config, label="../../etc/passwd").label == "etc-passwd", "never a path"
    assert options(config, label="x" * 64).label == "x" * 40
    for wrong, said in (
        ("--", "label needs a letter or a digit"),
        ("x" * 65, "label is text of up to 64"),
        (7, "text"),
    ):
        with pytest.raises(RecordingRefused, match=said):
            options(config, label=wrong)


def test_a_label_joins_the_recordings_name_and_it_still_is_a_recordings_name() -> None:
    when = datetime(2026, 10, 27, 10, 36, 25)
    assert recording_stem(when, "iPhone A Test", "lock-screen") == "20261027-103625-iphone-a-test-lock-screen"
    long = recording_stem(when, "A" * 80, "b" * 40)
    assert long == f"20261027-103625-{'a' * 23}-{'b' * 40}" and FILE_NAME.match(f"{long}.pr.mp4")
    assert recording_stem(when, "---", "x") == "20261027-103625-device-x"
    assert recording_stem(when, "iPhone") == "20261027-103625-iphone"


async def test_a_label_names_the_files_of_the_recording(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    recordings = recordings_over(FakeRenderer(), tmp_path / "r", [0.0])
    labelled = options(config, label="Unlock flow")
    await recordings.start(instance, config, rig.manager.control(instance), by="Ann", options=labelled)
    [kept] = (await recordings.stop(instance))["recording"]["files"]
    assert kept["name"].endswith("-simmirror-alpha-tp-1-unlock-flow.mp4") and FILE_NAME.match(kept["name"])


def test_the_nth_recording_of_a_name_says_which_and_it_still_is_a_recordings_name() -> None:
    when = datetime(2026, 10, 27, 10, 36, 25)
    assert recording_stem(when, "iPhone", nth=1) == "20261027-103625-iphone"
    assert recording_stem(when, "iPhone", "lock", nth=2) == "20261027-103625-iphone-lock-2"
    longest = recording_stem(when, "A" * 80, "b" * 40, nth=12)
    assert longest == f"20261027-103625-{'a' * 20}-{'b' * 40}-12" and FILE_NAME.match(f"{longest}.pr.mp4")
    assert FILE_NAME.match(f"{recording_stem(when, 'A' * 80, nth=3)}.gif")
    assert recording_stem(when, "---", nth=2) == "20261027-103625-device-2"


async def test_two_recordings_of_a_device_begun_in_the_same_second_are_both_kept(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    folder = tmp_path / "r"
    recordings = recordings_over(FakeRenderer(), folder, [0.0])
    control = rig.manager.control(instance)
    kept = []
    for _ in range(3):
        await recordings.start(instance, config, control, by="Ann", options=options(config, also="pr"))
        kept.append((await recordings.stop(instance))["recording"])
    first, second, third = (recording["id"] for recording in kept)
    assert (second, third) == (f"{first}-2", f"{first}-3"), "the clock's second is the same for all three"
    assert sorted(recording["id"] for recording in recordings.listing(config)) == [first, second, third]
    for recording in kept:
        assert json.loads((folder / f"{recording['id']}.json").read_text()) == recording
        assert all(FILE_NAME.match(file["name"]) for file in recording["files"])
    assert len(list(folder.glob("*.mp4"))) == 6, "each recording's movie and its pr copy, none written over"


class Unhurried(SimctlRecorder):
    """A simulator's own recorder that lets others run before it writes anything, as a real one does."""

    async def __call__(self, instance: Any, path: Path, log: Path, codec: str) -> Any:
        await asyncio.sleep(0)
        return await super().__call__(instance, path, log, codec)


async def test_two_devices_of_one_name_begun_together_are_named_apart(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    one, two = await rig.up(), await rig.up("tp-2")
    two.name = one.name
    config = rig.config.get(TP1)
    recordings = recordings_over(FakeRenderer(), tmp_path / "r", [0.0], tool_recorder=Unhurried())
    first, second = await asyncio.gather(
        *(
            recordings.start(device, config, rig.manager.control(device), by="Ann", options=options(config))
            for device in (one, two)
        )
    )
    assert second.id == f"{first.id}-2", "named before either has written a file"
    kept = [(await recordings.stop(device))["recording"]["id"] for device in (one, two)]
    assert kept == [first.id, second.id]


class SilentOnce(SimctlRecorder):
    """A simulator's own recorder that says nothing the first time, so that recording does not start, then starts."""

    def __init__(self) -> None:
        super().__init__(says="")

    async def __call__(self, instance: Any, path: Path, log: Path, codec: str) -> Any:
        process = await super().__call__(instance, path, log, codec)
        self.says = "Recording started\n"
        return process


async def test_a_recording_that_did_not_start_leaves_its_name_free(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    config = rig.config.get(TP1)
    folder = tmp_path / "r"
    recordings = recordings_over(FakeRenderer(), folder, [0.0], tool_recorder=SilentOnce())
    control = rig.manager.control(instance)
    with pytest.raises(RecordingRefused, match="the recording did not start"):
        await recordings.start(instance, config, control, by="Ann", options=options(config))
    assert list(folder.iterdir()) == [], "what it began to write is gone"
    run = await recordings.start(instance, config, control, by="Ann", options=options(config))
    assert run.id == recording_stem(WHEN.astimezone(), instance.name)
    await recordings.stop(instance)
