# SPDX-License-Identifier: Apache-2.0
"""Shrinking a JPEG with sips: the argv it runs, what it answers, and nothing left behind."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sim_mirror.platform import images


async def test_sips_is_asked_for_the_width_and_quality_and_its_output_is_answered() -> None:
    seen: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(argv)
        assert Path(argv[-3]).read_bytes() == b"original"
        Path(argv[-1]).write_bytes(b"smaller")
        return 0, ""

    assert await images.resize_jpeg(b"original", max_width=400, quality=70, run=run) == b"smaller"
    argv = seen[0]
    assert argv[:9] == ("sips", "--resampleWidth", "400", "-s", "format", "jpeg", "-s", "formatOptions", "70")
    assert argv[-2] == "--out" and not Path(argv[-1]).parent.exists()


async def test_a_sips_that_fails_or_writes_nothing_answers_none() -> None:
    async def fails(argv: Sequence[str]) -> tuple[int, str]:
        return 1, ""

    async def writes_nothing(argv: Sequence[str]) -> tuple[int, str]:
        return 0, ""

    assert await images.resize_jpeg(b"x", max_width=400, quality=70, run=fails) is None
    assert await images.resize_jpeg(b"x", max_width=400, quality=70, run=writes_nothing) is None
