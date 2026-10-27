# SPDX-License-Identifier: Apache-2.0
"""`sim_record`: an agent records a demo -- start, its steps, stop -- and is told where each file it kept is."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.recordings import RecordingRefused, Recordings, Rendered, RenderJob
from sim_mirror.protocol import Recording
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.recording import FakeRenderer, SimctlRecorder
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.record import describe, stopped
from sim_mirror.tools.record import rendering as rendering_text
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")
REGISTRY = ToolRegistry()


def context(rig: DeviceRig, recordings: Recordings | None) -> ToolContext:
    return ToolContext(
        manager=rig.manager,
        actions=AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait),
        caller=CALLER,
        config=rig.config.get(CALLER.scope),
        copy=rig.copy,
        sleep=no_wait,
        recordings=recordings,
    )


async def record(rig: DeviceRig, recordings: Recordings | None, arguments: dict[str, Any]) -> Result:
    return await REGISTRY.call("sim_record", arguments, context(rig, recordings))


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


async def test_an_agent_records_a_demo_and_is_told_where_it_was_kept(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    folder = tmp_path / "recordings"
    recordings = Recordings(
        FakeRenderer(sizes={"mp4": 2_500_000, "gif": 40_000}, duration_s=6.2),
        tool_recorder=SimctlRecorder(),
        folder_for=lambda config: folder,
    )
    assert said(await record(rig, recordings, {})) == "nothing is being recorded; sim_record start begins"
    assert said(await record(rig, recordings, {"action": "list"})) == "no recordings kept yet"
    started = await record(rig, recordings, {"action": "start", "format": "both", "speed": "2"})
    assert said(started) == (
        "recording SimMirror · alpha · tp-1 as both; sim_record stop keeps it (it stops by itself after 300s)"
    )
    assert said(await record(rig, recordings, {"action": "status"})).startswith(
        "recording SimMirror · alpha · tp-1 for 0s, started by Claude · notes"
    )
    again = await record(rig, recordings, {"action": "start"})
    assert again["isError"] and "already being recorded" in said(again)
    kept = said(await record(rig, recordings, {"action": "stop"}))
    lines = kept.splitlines()
    assert lines[0].startswith("kept 2")
    assert lines[0].endswith("-simmirror-alpha-tp-1 · SimMirror · alpha · tp-1 · 6.2s")
    assert lines[1].startswith("mp4 400x868 · 2.5 MB · ") and lines[1].endswith(".mp4")
    assert lines[2].startswith("gif 400x868 · 40 KB · ") and lines[2].endswith(".gif")
    listed = said(await record(rig, recordings, {"action": "list"}))
    assert listed == kept.removeprefix("kept ")


async def test_what_a_recording_cannot_be_is_refused(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    recordings = Recordings(
        FakeRenderer(sizes={"mp4": 2_500_000, "gif": 40_000}, duration_s=6.2),
        tool_recorder=SimctlRecorder(),
        folder_for=lambda config: tmp_path,
    )
    for arguments, refusal in (
        ({"action": "rewind"}, "action is one of start, stop, status, list"),
        ({"action": "stop"}, "nothing is being recorded"),
        ({"action": "start", "format": "webm"}, "format is one of mp4, gif, both"),
        ({"action": "start", "touches": "yes"}, "touches is true or false"),
        ({"action": "start", "preset": "slack"}, "preset is one of pr"),
        ({"action": "start", "width": 9000}, "width is a whole number of pixels from 240 to 1200"),
        ({"action": "start", "quality": "best"}, "quality is one of low, medium, high"),
    ):
        refused = await record(rig, recordings, arguments)
        assert refused["isError"] and said(refused) == refusal, said(refused)
    unavailable = await record(rig, None, {"action": "list"})
    assert unavailable["isError"] and said(unavailable) == "recording is not available here"
    await rig.up()
    idle = await record(rig, recordings, {"action": "stop"})
    assert idle["isError"] and said(idle).endswith("is not being recorded")


async def test_an_agents_tap_while_recording_is_drawn_where_it_landed(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    renderer = FakeRenderer(sizes={"mp4": 2_500_000, "gif": 40_000}, duration_s=6.2)
    recordings = Recordings(renderer, tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path)
    ctx = context(rig, recordings)
    await REGISTRY.call("sim_record", {"action": "start"}, ctx)
    tapped = await REGISTRY.call("sim_act", {"steps": [{"tap": [201, 437]}]}, ctx)
    assert not tapped["isError"], said(tapped)
    await REGISTRY.call("sim_record", {"action": "stop"}, ctx)
    [touch] = renderer.jobs[0].touches
    assert (touch.kind, touch.by, touch.points) == ("tap", "agent", ((0.5, 0.5),))


async def test_an_agent_records_for_a_pull_request_with_its_preset_or_its_own_width(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    renderer = FakeRenderer()
    recordings = Recordings(renderer, tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path)
    for arguments in ({"preset": "pr"}, {"format": "both", "width": 600, "quality": "medium"}):
        started = await record(rig, recordings, {"action": "start", **arguments})
        assert not started["isError"], said(started)
        await record(rig, recordings, {"action": "stop"})
    pr, own = renderer.jobs
    assert (pr.mp4_width, pr.mp4_quality, pr.codec, pr.gif) == (450, "low", "h264", None)
    assert (own.mp4_width, own.gif_width, own.mp4_quality, own.gif is not None) == (600, 600, "medium", True)


async def test_stop_keeps_a_copy_for_a_pull_request_beside_the_full_size_file_when_asked(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    renderer = FakeRenderer()
    recordings = Recordings(renderer, tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path)
    for start, stop in (({"also": "pr"}, {}), ({}, {"also": "pr"})):
        assert not (await record(rig, recordings, {"action": "start", **start}))["isError"]
        kept = said(await record(rig, recordings, {"action": "stop", **stop})).splitlines()
        assert kept[1].startswith("mp4 400x868 · 2 KB · ") and kept[1].endswith(".mp4")
        assert kept[2].startswith("mp4 400x868 · 2 KB · ") and kept[2].endswith(".pr.mp4")
        assert kept[3].startswith("note: ") and kept[3].endswith(".pr.mp4 is the pr copy, beside the recording")
    assert [job.mp4_width for job in renderer.jobs] == [0, 450, 0, 450]
    assert not (await record(rig, recordings, {"action": "start"}))["isError"]
    wrong = await record(rig, recordings, {"action": "stop", "also": "gif"})
    assert wrong["isError"] and said(wrong) == "also is one of pr: a copy kept beside the recording"
    refused = await record(rig, recordings, {"action": "start", "also": "gif"})
    assert refused["isError"] and "already being recorded" not in said(refused)


def test_a_recording_described_without_pixels_or_with_a_note() -> None:
    assert (
        describe(
            {
                "id": "r",
                "device": "iPhone",
                "started_at": "2026-09-29T00:00:00Z",
                "duration_ms": 1500,
                "files": [{"name": "r.mp4", "path": "/r.mp4", "format": "mp4", "bytes": 10, "width": 0, "height": 0}],
                "notes": ["kept as recorded"],
            }
        )
        == "r · iPhone · 1.5s\nmp4 · 1 KB · /r.mp4\nnote: kept as recorded"
    )
    assert dataclasses.is_dataclass(Rendered)


async def instantly(delay: float) -> None:
    return None


class Held(FakeRenderer):
    """Renders once `go` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.go = asyncio.Event()

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        await self.go.wait()
        return await super().render(job, config)


async def test_an_agent_says_how_long_a_recording_may_run_and_what_its_file_is_called(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    recordings = Recordings(FakeRenderer(), tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path)
    started = await record(rig, recordings, {"action": "start", "max_seconds": 2400, "label": "Firmware update"})
    assert said(started).endswith("(it stops by itself after 2400s)")
    kept = said(await record(rig, recordings, {"action": "stop"}))
    assert kept.splitlines()[1].endswith("-simmirror-alpha-tp-1-firmware-update.mp4")
    for arguments, refusal in (
        ({"max_seconds": 4}, "max_seconds is a whole number of seconds from 5 to 3600"),
        ({"label": "?!"}, "label needs a letter or a digit: it is joined into the file's name"),
    ):
        refused = await record(rig, recordings, {"action": "start", **arguments})
        assert refused["isError"] and said(refused) == refusal


async def test_a_recording_that_stopped_by_itself_is_told_by_status_and_answered_by_stop(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    recordings = Recordings(
        FakeRenderer(), tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path, sleep=instantly
    )
    await record(rig, recordings, {"action": "start", "max_seconds": 300})
    instance = rig.manager.instance(CALLER.scope)
    assert instance is not None and instance.recording is not None
    run = instance.recording
    assert run.watchdog is not None and not isinstance(run, str)
    await run.watchdog
    assert run.finishing is not None
    await run.finishing
    status = said(await record(rig, recordings, {"action": "status"}))
    assert status == f"{run.id} stopped by itself at 0s, its limit, and is kept; sim_record stop answers with its files"
    stopped = said(await record(rig, recordings, {"action": "stop"})).splitlines()
    assert stopped[0] == f"stopped by itself at 0s, its limit; kept {run.id} · SimMirror · alpha · tp-1 · 2.5s"
    assert stopped[-1] == "note: it stopped by itself after 300s, its limit"
    assert said(await record(rig, recordings, {"action": "status"})).startswith("nothing is being recorded")
    failing = Recordings(
        FakeRenderer(refuse="no helper"), tool_recorder=SimctlRecorder(), folder_for=lambda c: tmp_path, sleep=instantly
    )
    await record(rig, failing, {"action": "start", "format": "gif"})
    lost = instance.recording
    assert lost is not None and not isinstance(lost, str) and lost.watchdog is not None
    await lost.watchdog
    assert lost.finishing is not None
    with contextlib.suppress(RecordingRefused):
        await lost.finishing
    status = said(await record(rig, failing, {"action": "status"}))
    assert status.startswith(f"{lost.id} stopped by itself at 0s, its limit, and could not be kept: no helper; ")


async def test_a_long_render_is_said_by_stop_status_and_list_until_it_is_kept(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    renderer = Held()
    recordings = Recordings(
        renderer, tool_recorder=SimctlRecorder(), folder_for=lambda config: tmp_path, stop_wait=0.01
    )
    await record(rig, recordings, {"action": "start"})
    instance = rig.manager.instance(CALLER.scope)
    assert instance is not None
    run = instance.recording
    assert run is not None and not isinstance(run, str)
    stopped = said(await record(rig, recordings, {"action": "stop"})).splitlines()
    path = tmp_path / f"{run.id}.mp4"
    assert stopped[0] == f"still rendering {run.id} · SimMirror · alpha · tp-1 · 0.0s"
    assert (
        stopped[1]
        == f"note: still being rendered; it will be kept at {path}, and listed with the recordings once it is"
    )
    assert stopped[2] == "sim_record list names it as rendering until it is kept, then with its files"
    rendering = f"rendering {run.id} · SimMirror · alpha · tp-1 (stopped at 0s); it will be kept at {path}"
    assert said(await record(rig, recordings, {"action": "status"})) == rendering
    assert said(await record(rig, recordings, {"action": "list"})) == rendering
    renderer.go.set()
    assert run.finishing is not None
    await run.finishing
    assert said(await record(rig, recordings, {"action": "list"})).startswith(f"{run.id} · ")
    assert said(await record(rig, recordings, {"action": "status"})).startswith("nothing is being recorded")


def test_what_stop_answers_reads_an_older_servers_answer_too() -> None:
    recording: Recording = {
        "id": "r",
        "device": "iPhone",
        "started_at": "2026-09-29T00:00:00Z",
        "duration_ms": 1500,
        "files": [],
        "notes": [],
    }
    older: Any = {"recording": recording}
    assert stopped(older, ask="") == "kept r · iPhone · 1.5s"
    by_itself = rendering_text(
        {
            "id": "r",
            "device": "iPhone",
            "started_at": "x",
            "recorded_ms": 300_000,
            "stopped_by_itself": True,
            "paths": ["/r.mp4", "/r.gif"],
        }
    )
    assert by_itself == "rendering r · iPhone (stopped by itself at 300s); it will be kept at /r.mp4 and /r.gif"
