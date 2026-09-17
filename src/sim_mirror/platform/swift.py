# SPDX-License-Identifier: Apache-2.0
"""Helpers SimMirror compiles for itself from Swift it ships -- the one place ``swiftc`` is run.

Some of what SimMirror does needs a macOS framework Python cannot reach: Vision reads text in a screenshot's pixels. A
helper for it is a Swift source file in the package, compiled on first use with the Xcode a scope uses
(``device.developer_dir``) through ``xcrun`` -- so no binary is shipped, signed or trusted beyond the Xcode already
trusted to build apps -- and kept under a folder named for a hash of the source and the compiler's version: a new
SimMirror or a new Xcode compiles again, and nothing else does.

A build writes into a private folder of its own and moves the finished binary into place in one step, so a helper
found is always whole, and two builds of the same helper can only both succeed.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.storage.private import ensure_private_dir

#: How long compiling a helper may take: the first build of a machine fills Swift's module cache, which is slow.
BUILD_TIMEOUT_S = 300.0
VERSION_TIMEOUT_S = 30.0
#: The shared cache of compiled framework modules, inside the helpers' folder.
MODULE_CACHE = "module-cache"


class SwiftBuildError(Exception):
    """A helper that could not be compiled: no Swift with this Xcode, or the compiler refused it."""


def _swiftc(*args: str) -> tuple[str, ...]:
    return ("--sdk", "macosx", "swiftc", *args)


async def swift_version(developer_dir: str, *, xcrun: XcrunRunner = run_xcrun) -> str:
    """What ``swiftc --version`` says with this Xcode. Raises `SwiftBuildError` when there is no Swift."""
    result = await xcrun(*_swiftc("--version"), timeout=VERSION_TIMEOUT_S, developer_dir=developer_dir)
    version = (result.out or result.err).strip()
    if not result.ok or not version:
        raise SwiftBuildError(f"Swift is not available with this Xcode: {result.message}")
    return version


def build_key(source: bytes, version: str) -> str:
    """The folder a helper built from `source` by a compiler of `version` is kept in."""
    return hashlib.sha256(source + b"\0" + version.encode()).hexdigest()[:16]


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


async def built_helper(
    source: bytes, *, name: str, folder: Path, developer_dir: str, xcrun: XcrunRunner = run_xcrun
) -> Path:
    """The helper `name` compiled from `source` with this Xcode's Swift, compiling it only when no build of the same
    source by the same compiler is kept under `folder`. Raises `SwiftBuildError`."""
    version = await swift_version(developer_dir, xcrun=xcrun)
    target = folder / build_key(source, version) / name
    if _runnable(target):
        return target
    ensure_private_dir(target.parent)
    cache = ensure_private_dir(folder / MODULE_CACHE)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=target.parent) as work:
        source_file, output = Path(work) / f"{name}.swift", Path(work) / name
        source_file.write_bytes(source)
        argv = _swiftc("-O", "-parse-as-library", "-module-cache-path", str(cache), "-o", str(output), str(source_file))
        result = await xcrun(*argv, timeout=BUILD_TIMEOUT_S, developer_dir=developer_dir)
        if not result.ok or not _runnable(output):
            raise SwiftBuildError(f"compiling {name} failed: {result.message}")
        os.replace(output, target)
    return target
