# SPDX-License-Identifier: Apache-2.0
"""Rendering recordings with SimMirror's native helper, the same one the native connector runs.

The helper is found as the connector finds it -- ``connectors.native.helper_path``, the one in the package, or the one
``sim-mirror helper build`` built -- and used only when it is this version's and says it can render. The job travels
as a private JSON file beside the recording, never in argv.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.native.connector import default_candidates
from sim_mirror.connectors.native.helper import HelperVersion, helper_version, locate_helper, render_recording
from sim_mirror.core.recordings import RecordingRefused, Rendered, RenderJob
from sim_mirror.host_copy import HostCopy

FEATURE = "render"


class HelperRenderer:
    """A `Renderer` that asks the native helper."""

    def __init__(
        self,
        *,
        copy: HostCopy | None = None,
        candidates: Callable[[], Sequence[Path]] = default_candidates,
        ask_version: Callable[[str], Awaitable[HelperVersion | None]] = helper_version,
        render: Callable[[str, Path], Awaitable[dict[str, Any]]] = render_recording,
    ) -> None:
        self._copy = copy or HostCopy()
        self._candidates = candidates
        self._ask_version = ask_version
        self._render = render

    async def _helper(self, config: SimConfig) -> tuple[str | None, str | None]:
        """The helper to render with, or why there is none."""
        found = await locate_helper(config.native_helper_path, self._candidates(), self._ask_version)
        why = found.reason(self._copy, config.native_helper_path)
        if why is not None or found.binary is None or found.version is None:
            return None, why or self._copy.helper_missing(config.native_helper_path)
        if FEATURE not in found.version.features:
            build = self._copy.helper_build_command
            return None, f"the native helper at {found.binary} cannot render recordings; build it again with `{build}`"
        return found.binary, None

    async def why_not(self, config: SimConfig) -> str | None:
        return (await self._helper(config))[1]

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        binary, why = await self._helper(config)
        if binary is None:
            raise RecordingRefused(why or "there is no native helper to render with")
        path = job.input.with_name(job.input.name + ".job.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(job.to_json(), out)
        try:
            answer = await self._render(binary, path)
        finally:
            with contextlib.suppress(OSError):
                path.unlink()
        if "error" in answer:
            raise RecordingRefused(f"the recording could not be rendered: {answer['error']}")
        try:
            return [
                Rendered(
                    Path(str(made["path"])),
                    "gif" if made["format"] == "gif" else "mp4",
                    int(made["width"]),
                    int(made["height"]),
                    float(made["duration_s"]),
                )
                for made in answer["files"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise RecordingRefused(f"the native helper said what it rendered unreadably: {exc}") from exc
