# SPDX-License-Identifier: Apache-2.0
"""Building a Swift package with the Xcode a scope names: ``xcrun swift build``.

SimMirror's native helper ships built inside its wheel; this is how an install that has no built copy -- a source
checkout, an sdist, a platform the wheel was not built for -- builds its own. The build runs with ``DEVELOPER_DIR`` set
to the chosen Xcode, never ``xcode-select``, for both Mac architectures when that Xcode can, and says why when it
cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

#: How long a release build may take: a first one resolves and compiles everything.
BUILD_TIMEOUT_S = 900.0
#: The architectures a universal helper is built for.
UNIVERSAL = ("arm64", "x86_64")


@dataclass(frozen=True)
class SwiftBuild:
    """Where a build put its products, or why it did not build."""

    products: Path | None
    #: The last line the build printed that says what went wrong; empty when it built.
    failure: str = ""


def build_argv(package: Path, scratch: Path, archs: tuple[str, ...]) -> tuple[str, ...]:
    """``swift build`` of a package for release, into a scratch folder of SimMirror's own."""
    flags = tuple(flag for arch in archs for flag in ("--arch", arch))
    return ("swift", "build", "--package-path", str(package), "--scratch-path", str(scratch), "-c", "release", *flags)


async def build_package(
    package: Path,
    scratch: Path,
    *,
    developer_dir: str = "",
    archs: tuple[str, ...] = UNIVERSAL,
    xcrun: XcrunRunner = run_xcrun,
) -> SwiftBuild:
    """Build a package for release, answering the folder its products are in."""
    argv = build_argv(package, scratch, archs)
    built = await xcrun(*argv, timeout=BUILD_TIMEOUT_S, developer_dir=developer_dir)
    if not built.ok:
        return SwiftBuild(None, built.message)
    shown = await xcrun(*argv, "--show-bin-path", timeout=60.0, developer_dir=developer_dir)
    products = shown.out.strip().splitlines()[-1:] if shown.ok else []
    if not products:
        return SwiftBuild(None, f"the build finished, but did not say where its products are: {shown.message}")
    return SwiftBuild(Path(products[0]))
