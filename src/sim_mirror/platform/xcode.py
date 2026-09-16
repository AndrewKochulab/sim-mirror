# SPDX-License-Identifier: Apache-2.0
"""Which version an Xcode is, as its ``xcodebuild`` says. Which Xcode a program runs with is `developer_dir`'s."""

from __future__ import annotations

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

VERSION_TIMEOUT_S = 30.0


async def xcode_version(developer_dir: str = "", xcrun: XcrunRunner = run_xcrun) -> str | None:
    """``Xcode 26.6 (17F42)``: xcodebuild's own two lines as one -- or None when it does not answer."""
    result = await xcrun("xcodebuild", "-version", timeout=VERSION_TIMEOUT_S, developer_dir=developer_dir)
    lines = [line.strip() for line in result.out.splitlines() if line.strip()] if result.ok else []
    if not lines:
        return None
    build = next(
        (line.removeprefix("Build version").strip() for line in lines[1:] if line.startswith("Build version")), ""
    )
    return f"{lines[0]} ({build})" if build else lines[0]
