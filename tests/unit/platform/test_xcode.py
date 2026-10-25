# SPDX-License-Identifier: Apache-2.0
"""An Xcode's version, as its xcodebuild says it."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.platform.xcode import VERSION_TIMEOUT_S, xcode_installed, xcode_version
from sim_mirror.testing.fakes import FakeXcrun

DEVELOPER = "/Applications/Xcode.app/Contents/Developer"


async def test_xcodes_version_is_its_two_lines_as_one_or_none_when_it_does_not_answer() -> None:
    both = FakeXcrun().on("xcodebuild", "-version", out="Xcode 26.6\nBuild version 17F42\n")
    assert await xcode_version(DEVELOPER, both) == "Xcode 26.6 (17F42)"
    assert both.calls[-1].developer_dir == DEVELOPER and both.calls[-1].timeout == VERSION_TIMEOUT_S
    assert await xcode_version(xcrun=FakeXcrun().on("xcodebuild", "-version", out="Xcode 27.0\n")) == "Xcode 27.0"
    assert await xcode_version(xcrun=FakeXcrun().on("xcodebuild", "-version", out="\n")) is None
    refused = FakeXcrun().on("xcodebuild", "-version", rc=1, err="xcodebuild: error: tool requires Xcode")
    assert await xcode_version(xcrun=refused) is None


def test_an_xcode_is_the_one_named_else_one_in_the_applications_folder(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    applications.mkdir()
    assert not xcode_installed(applications=applications)
    (applications / "Xcode27.app" / "Contents" / "Developer").mkdir(parents=True)
    assert xcode_installed(applications=applications)
    assert xcode_installed(str(applications / "Xcode27.app" / "Contents" / "Developer"), applications=tmp_path)
    assert not xcode_installed(str(tmp_path / "gone"), applications=applications)
