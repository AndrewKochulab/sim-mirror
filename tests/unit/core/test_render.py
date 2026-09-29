# SPDX-License-Identifier: Apache-2.0
"""Recordings are rendered by the native helper this version ships, which says it can; its job travels as a private
file, and what it answers -- files, or why not -- is read with care."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.native import helper as helper_module
from sim_mirror.connectors.native import wire
from sim_mirror.connectors.native.helper import HelperVersion, helper_version, render_recording
from sim_mirror.core.recordings import RecordingRefused, RenderJob, Touch
from sim_mirror.core.render import HelperRenderer
from sim_mirror.host_copy import HostCopy

CONFIG = SimConfig.defaults()


def job(tmp_path: Path) -> RenderJob:
    return RenderJob(
        input=tmp_path / ".rec.raw",
        input_kind="frames",
        touches=(Touch(1.0, "tap", ((0.5, 0.5),), 0.1, "agent"),),
        mp4=tmp_path / "rec.mp4",
        gif=None,
        codec="h264",
        speed=2.0,
        gif_fps=12,
        gif_width=600,
    )


def renderer(tmp_path: Path, answer: dict[str, Any], *, features: tuple[str, ...] = ("render",)) -> HelperRenderer:
    binary = tmp_path / "sim-mirror-helper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    seen: dict[str, Any] = {}

    async def version(path: str) -> HelperVersion:
        return HelperVersion(__version__, wire.VERSION, None, features)

    async def render(path: str, job_file: Path) -> dict[str, Any]:
        seen["job"] = json.loads(job_file.read_text())
        seen["mode"] = stat.S_IMODE(job_file.stat().st_mode)
        return answer

    made = HelperRenderer(copy=HostCopy(), candidates=lambda: (binary,), ask_version=version, render=render)
    made.seen = seen  # type: ignore[attr-defined]
    return made


async def test_the_helper_renders_from_a_private_job_file_and_says_what_it_wrote(tmp_path: Path) -> None:
    mp4 = {"path": str(tmp_path / "rec.mp4"), "format": "mp4", "width": 400, "height": 868, "duration_s": 2.5}
    wrote = {"files": [mp4]}
    made = renderer(tmp_path, wrote)
    assert await made.why_not(CONFIG) is None
    [rendered] = await made.render(job(tmp_path), CONFIG)
    assert (rendered.format, rendered.width, rendered.height, rendered.duration_s) == ("mp4", 400, 868, 2.5)
    seen = made.seen  # type: ignore[attr-defined]
    assert seen["mode"] == 0o600 and seen["job"]["touches"][0] == {
        "t": 1.0,
        "kind": "tap",
        "points": [[0.5, 0.5]],
        "duration": 0.1,
        "by": "agent",
    }
    assert seen["job"]["input_kind"] == "frames" and seen["job"]["gif"] is None and seen["job"]["speed"] == 2.0
    assert not list(tmp_path.glob("*.job.json")), "the job file is gone"
    gif = {"files": [{"path": "/x.gif", "format": "gif", "width": 1, "height": 1, "duration_s": 1}]}
    assert (await renderer(tmp_path, gif).render(job(tmp_path), CONFIG))[0].format == "gif"


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ({"error": "the recording has no picture"}, "could not be rendered: the recording has no picture"),
        ({"files": [{"path": "/x"}]}, "said what it rendered unreadably"),
    ],
)
async def test_what_the_helper_refuses_or_garbles_is_refused(tmp_path: Path, answer: dict[str, Any], said: str) -> None:
    with pytest.raises(RecordingRefused, match=said):
        await renderer(tmp_path, answer).render(job(tmp_path), CONFIG)


async def test_no_helper_or_one_that_cannot_render_is_why_not(tmp_path: Path) -> None:
    old = renderer(tmp_path, {}, features=())
    why = await old.why_not(CONFIG)
    assert why is not None and "cannot render recordings; build it again with `sim-mirror helper build`" in why
    with pytest.raises(RecordingRefused, match="cannot render recordings"):
        await old.render(job(tmp_path), CONFIG)
    missing = HelperRenderer(candidates=lambda: (tmp_path / "nowhere",))
    assert "native helper is not built" in (await missing.why_not(CONFIG) or "")


async def test_render_recording_reads_the_helpers_answer_whatever_it_is(tmp_path: Path) -> None:
    def runner(code: int, out: str) -> Any:
        async def run(argv: Any) -> tuple[int, str]:
            assert tuple(argv) == ("/bin/helper", "render", "--job", str(tmp_path / "job.json"))
            return code, out

        return run

    job_file = tmp_path / "job.json"
    assert await render_recording("/bin/helper", job_file, runner(0, '{"files": []}')) == {"files": []}
    assert await render_recording("/bin/helper", job_file, runner(1, '{"error": "no"}')) == {"error": "no"}
    assert await render_recording("/bin/helper", job_file, runner(1, "{}")) == {
        "error": "the native helper failed (exit 1)"
    }
    assert await render_recording("/bin/helper", job_file, runner(124, "")) == {
        "error": "the native helper answered nothing readable (exit 124)"
    }
    assert await render_recording("/bin/helper", job_file, runner(0, "[1]")) == {
        "error": "the native helper answered nothing readable (exit 0)"
    }


async def test_rendering_may_take_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[float] = []

    async def run(argv: Any, *, timeout: float) -> tuple[int, str]:
        waited.append(timeout)
        return 0, '{"files": []}'

    monkeypatch.setattr(helper_module.process, "run", run)
    assert await render_recording("/bin/helper", Path("/tmp/job.json")) == {"files": []}
    assert waited == [helper_module.RENDER_TIMEOUT_S] and helper_module.RENDER_TIMEOUT_S >= 600


async def test_a_helper_says_what_it_can_do_and_an_older_one_says_nothing() -> None:
    async def said(out: str) -> HelperVersion | None:
        async def run(argv: Any) -> tuple[int, str]:
            return 0, out

        return await helper_version("/bin/helper", run)

    newer = await said(json.dumps({"version": "1.3.0", "wire": 1, "core_simulator": None, "features": ["render"]}))
    assert newer is not None and newer.features == ("render",)
    older = await said(json.dumps({"version": "1.2.0", "wire": 1, "core_simulator": "1051"}))
    assert older is not None and older.features == ()
