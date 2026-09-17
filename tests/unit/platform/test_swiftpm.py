# SPDX-License-Identifier: Apache-2.0
"""Building a Swift package: for release, both architectures, the chosen Xcode, and why when it does not build."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.platform.swiftpm import BUILD_TIMEOUT_S, UNIVERSAL, SwiftBuild, build_argv, build_package
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import FakeXcrun

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"


def test_a_build_is_for_release_into_its_scratch_folder_for_each_architecture() -> None:
    assert build_argv(Path("/src/helper"), Path("/state/build"), UNIVERSAL) == (
        "swift", "build", "--package-path", "/src/helper", "--scratch-path", "/state/build", "-c", "release",
        "--arch", "arm64", "--arch", "x86_64",
    )  # fmt: skip


async def test_a_package_that_builds_says_where_its_products_are() -> None:
    fake = FakeXcrun().on(
        "swift",
        "build",
        then=lambda args: XcrunResult(
            0, "/state/build/apple/Products/Release\n" if args[-1] == "--show-bin-path" else "Compiling\n", ""
        ),
    )
    built = await build_package(
        Path("/src"), Path("/state/build"), developer_dir=XCODE_27, archs=("arm64",), xcrun=fake
    )
    assert built == SwiftBuild(Path("/state/build/apple/Products/Release"))
    assert [call.developer_dir for call in fake.calls] == [XCODE_27, XCODE_27]
    assert fake.calls[0].timeout == BUILD_TIMEOUT_S and fake.calls[1].args[-1] == "--show-bin-path"


async def test_a_package_that_does_not_build_says_why() -> None:
    failing = FakeXcrun().on("swift", "build", rc=1, err="error: no such module 'SimulatorKit'\n")
    assert await build_package(Path("/src"), Path("/b"), xcrun=failing) == SwiftBuild(
        None, "error: no such module 'SimulatorKit'"
    )
    silent = FakeXcrun().on(
        "swift", "build", then=lambda args: XcrunResult(1 if args[-1] == "--show-bin-path" else 0, "", "lost")
    )
    unsaid = await build_package(Path("/src"), Path("/b"), xcrun=silent)
    assert (
        unsaid.products is None and unsaid.failure == "the build finished, but did not say where its products are: lost"
    )
