# SPDX-License-Identifier: Apache-2.0
"""Which Xcode is selected, and its version as xcodebuild says it."""

from __future__ import annotations

from collections.abc import Sequence

from sim_mirror.platform.xcode import VERSION_TIMEOUT_S, selected_developer_dir, xcode_version
from sim_mirror.testing.fakes import FakeXcrun

DEVELOPER = "/Applications/Xcode.app/Contents/Developer"


async def test_the_selected_xcode_is_the_folder_xcode_select_names() -> None:
    asked: list[tuple[str, ...]] = []

    async def selected(argv: Sequence[str]) -> tuple[int, str]:
        asked.append(tuple(argv))
        return 0, f"{DEVELOPER}\n"

    async def unselected(argv: Sequence[str]) -> tuple[int, str]:
        return 2, "xcode-select: error: unable to get active developer directory"

    async def blank(argv: Sequence[str]) -> tuple[int, str]:
        return 0, "  \n"

    assert await selected_developer_dir(selected) == DEVELOPER and asked == [("xcode-select", "-p")]
    assert await selected_developer_dir(unselected) is None and await selected_developer_dir(blank) is None


async def test_xcodes_version_is_its_two_lines_as_one_or_none_when_it_does_not_answer() -> None:
    both = FakeXcrun().on("xcodebuild", "-version", out="Xcode 26.6\nBuild version 17F42\n")
    assert await xcode_version(DEVELOPER, both) == "Xcode 26.6 (17F42)"
    assert both.calls[-1].developer_dir == DEVELOPER and both.calls[-1].timeout == VERSION_TIMEOUT_S
    assert await xcode_version(xcrun=FakeXcrun().on("xcodebuild", "-version", out="Xcode 27.0\n")) == "Xcode 27.0"
    assert await xcode_version(xcrun=FakeXcrun().on("xcodebuild", "-version", out="\n")) is None
    refused = FakeXcrun().on("xcodebuild", "-version", rc=1, err="xcodebuild: error: tool requires Xcode")
    assert await xcode_version(xcrun=refused) is None
