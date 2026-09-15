# SPDX-License-Identifier: Apache-2.0
"""Which Xcode this Mac uses, and which version it is -- as ``xcode-select`` and ``xcodebuild`` say."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from sim_mirror.platform import process
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

Runner = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]
VERSION_TIMEOUT_S = 30.0


async def selected_developer_dir(run: Runner = process.run) -> str | None:
    """The developer folder ``xcode-select`` names, or None when none is selected."""
    code, out = await run(("xcode-select", "-p"))
    path = out.strip()
    return path if code == 0 and path else None


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
