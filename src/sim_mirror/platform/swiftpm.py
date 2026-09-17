# SPDX-License-Identifier: Apache-2.0
"""Building a Swift package with the Xcode a scope names: ``xcrun swift build``.

SimMirror's native helper ships built inside its wheel; this is how an install that has no built copy -- a source
checkout, an sdist, a platform the wheel was not built for -- builds its own. The build runs with ``DEVELOPER_DIR`` set
to the chosen Xcode, never ``xcode-select``, for this Mac's own architecture -- the helper only ever runs where it was
built, and SwiftPM's multi-architecture build mishandles per-target Swift language modes on some toolchains -- and
says why when it cannot build. A release's universal helper is built by ``make helper-release``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

#: How long a release build may take: a first one resolves and compiles everything.
BUILD_TIMEOUT_S = 900.0


@dataclass(frozen=True)
class SwiftBuild:
    """Where a build put its products, or why it did not build."""

    products: Path | None
    #: The last line the build printed that says what went wrong; empty when it built.
    failure: str = ""


def build_argv(package: Path, scratch: Path) -> tuple[str, ...]:
    """``swift build`` of a package for release, for this Mac, into a scratch folder of SimMirror's own."""
    return ("swift", "build", "--package-path", str(package), "--scratch-path", str(scratch), "-c", "release")


async def build_package(
    package: Path,
    scratch: Path,
    *,
    developer_dir: str = "",
    xcrun: XcrunRunner = run_xcrun,
) -> SwiftBuild:
    """Build a package for release, answering the folder its products are in."""
    argv = build_argv(package, scratch)
    built = await xcrun(*argv, timeout=BUILD_TIMEOUT_S, developer_dir=developer_dir)
    if not built.ok:
        return SwiftBuild(None, built.message)
    shown = await xcrun(*argv, "--show-bin-path", timeout=60.0, developer_dir=developer_dir)
    products = shown.out.strip().splitlines()[-1:] if shown.ok else []
    if not products:
        return SwiftBuild(None, f"the build finished, but did not say where its products are: {shown.message}")
    return SwiftBuild(Path(products[0]))
