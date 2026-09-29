# SPDX-License-Identifier: Apache-2.0
"""Recording as tests see it: a renderer that writes the files a job names, and a simulator's own recorder.

`FakeRenderer` stands where the native helper's ``render`` does (`core.render.HelperRenderer`), and `SimctlRecorder`
where ``simctl io recordVideo`` does: neither runs anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.recordings import RecordingRefused, Rendered, RenderJob
from sim_mirror.testing.fakes import FakeProcess


class FakeRenderer:
    """Renders by writing each file the job names, of the sizes asked, or refuses as a test says."""

    def __init__(
        self,
        *,
        refuse: str | None = None,
        missing: str | None = None,
        sizes: Mapping[str, int] | None = None,
        duration_s: float = 2.5,
    ) -> None:
        self.jobs: list[RenderJob] = []
        self.refuse = refuse
        self.missing = missing
        self.sizes = dict(sizes or {"mp4": 1500, "gif": 1500})
        self.duration_s = duration_s

    async def why_not(self, config: SimConfig) -> str | None:
        return self.missing

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        self.jobs.append(job)
        if self.refuse:
            raise RecordingRefused(self.refuse)
        made = []
        for path, kind in ((job.mp4, "mp4"), (job.gif, "gif")):
            if path is not None:
                path.write_bytes(b"x" * self.sizes[kind])
                made.append(Rendered(path, kind, 400, 868, self.duration_s))  # type: ignore[arg-type]
        return made


class SimctlRecorder:
    """A simulator's own recording: a process that says it started, and writes its movie when interrupted."""

    def __init__(self, *, says: str = "Recording started\n") -> None:
        self.says = says
        self.started: list[tuple[str, str]] = []

    async def __call__(self, instance: DeviceInstance, path: Path, log: Path, codec: str) -> FakeProcess:
        self.started.append((instance.udid, codec))
        log.write_text(self.says)
        path.write_bytes(b"movie")
        process = FakeProcess(0)
        process.finish(0)
        return process
