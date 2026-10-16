# SPDX-License-Identifier: Apache-2.0
"""`sim_record`: an agent records a demo -- start, its steps, stop -- and is told where each file it kept is."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from sim_mirror.core.actions import AgentActions
from sim_mirror.core.recordings import Recordings, Rendered
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.recording import FakeRenderer, SimctlRecorder
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.record import describe
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
