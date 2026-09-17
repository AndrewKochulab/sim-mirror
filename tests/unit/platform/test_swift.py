# SPDX-License-Identifier: Apache-2.0
"""Swift helpers compiled once per source and compiler, with the scope's Xcode, and kept whole."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from sim_mirror.platform.swift import MODULE_CACHE, SwiftBuildError, build_key, built_helper, swift_version
from sim_mirror.testing.fakes import SWIFT_VERSION, FakeXcrun

XCODE = "/Applications/Xcode.app/Contents/Developer"
SOURCE = b"@main struct Helper { static func main() {} }\n"


async def test_the_version_is_what_swiftc_says_with_the_scopes_xcode() -> None:
    xcrun = FakeXcrun().with_swift()
    assert await swift_version(XCODE, xcrun=xcrun) == SWIFT_VERSION
    assert xcrun.calls[0].args == ("--sdk", "macosx", "swiftc", "--version") and xcrun.calls[0].developer_dir == XCODE
    said_on_stderr = FakeXcrun().on("--sdk", "macosx", "swiftc", "--version", err="Apple Swift version 6.3\n")
    assert await swift_version("", xcrun=said_on_stderr) == "Apple Swift version 6.3"


@pytest.mark.parametrize(
    "xcrun",
    [
        FakeXcrun().on("--sdk", rc=72, err='xcrun: error: unable to find utility "swiftc", not a developer tool'),
        FakeXcrun().on("--sdk", out=""),
    ],
)
async def test_no_swift_with_an_xcode_is_said(xcrun: FakeXcrun) -> None:
    with pytest.raises(SwiftBuildError, match="Swift is not available with this Xcode"):
        await swift_version(XCODE, xcrun=xcrun)


def test_a_build_is_kept_for_its_source_and_its_compiler() -> None:
    key = build_key(SOURCE, SWIFT_VERSION)
    assert len(key) == 16 and key == build_key(SOURCE, SWIFT_VERSION)
    assert key != build_key(SOURCE + b"//", SWIFT_VERSION) and key != build_key(SOURCE, "Apple Swift version 7")


async def test_a_helper_is_compiled_once_into_a_private_folder_and_then_found(tmp_path: Path) -> None:
    xcrun = FakeXcrun().with_swift()
    helper = await built_helper(SOURCE, name="probe", folder=tmp_path, developer_dir=XCODE, xcrun=xcrun)
    assert helper == tmp_path / build_key(SOURCE, SWIFT_VERSION) / "probe"
    assert os.access(helper, os.X_OK) and stat.S_IMODE(helper.parent.stat().st_mode) == 0o700
    compile_call = xcrun.calls[1]
    output, source_file = compile_call.args[-2], compile_call.args[-1]
    assert compile_call.args[:6] == ("--sdk", "macosx", "swiftc", "-O", "-parse-as-library", "-module-cache-path")
    assert compile_call.args[6] == str(tmp_path / MODULE_CACHE) and compile_call.developer_dir == XCODE
    assert Path(output).name == "probe" and Path(source_file).name == "probe.swift"
    assert Path(output).parent.parent == helper.parent and not Path(output).parent.exists()
    assert sorted(path.name for path in helper.parent.iterdir()) == ["probe"]

    again = await built_helper(SOURCE, name="probe", folder=tmp_path, developer_dir=XCODE, xcrun=xcrun)
    assert again == helper and [call.args[3] for call in xcrun.calls] == ["--version", "-O", "--version"]


async def test_a_compiler_that_refuses_the_source_or_writes_nothing_is_said(tmp_path: Path) -> None:
    refused = FakeXcrun().with_swift(compiles=False)
    with pytest.raises(
        SwiftBuildError, match=re.escape("compiling probe failed: main.swift:3:1: error: cannot find 'VNThing'")
    ):
        await built_helper(SOURCE, name="probe", folder=tmp_path, developer_dir=XCODE, xcrun=refused)
    silent = FakeXcrun().with_swift().on("--sdk", "macosx", "swiftc", "-O")
    with pytest.raises(SwiftBuildError, match="compiling probe failed: xcrun exited with 0"):
        await built_helper(SOURCE, name="probe", folder=tmp_path, developer_dir=XCODE, xcrun=silent)
    assert list((tmp_path / build_key(SOURCE, SWIFT_VERSION)).iterdir()) == []
