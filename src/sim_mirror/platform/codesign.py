# SPDX-License-Identifier: Apache-2.0
"""Signing a simulator's app bundle again, after SimMirror changed its Info.plist (`build.stamp`).

An app's signature seals its Info.plist: change one key and ``codesign --verify`` calls the bundle modified. Xcode signs
an app built for a simulator *ad hoc* -- ``Signature=adhoc``, no team, no certificate -- so signing the changed bundle
the same way gives it a signature as good as the one Xcode made, with its identifier, entitlements and flags kept.
Nested frameworks keep their own signatures: only the bundle's own Info.plist changed, so only it is signed again.

A real device's app is signed for that device by a team's certificate and profile, which SimMirror does not hold, so
nothing here signs one; `build.stamp` never asks it to.
"""

from __future__ import annotations

from pathlib import Path

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

#: How long signing an app may take: a large app's resources are hashed again.
RESIGN_TIMEOUT_S = 120.0


def resign_argv(app: Path) -> tuple[str, ...]:
    return (
        "codesign",
        "--force",
        "--sign",
        "-",
        "--preserve-metadata=identifier,entitlements,flags,runtime",
        "--timestamp=none",
        str(app),
    )


async def resign_adhoc(app: Path, *, developer_dir: str = "", xcrun: XcrunRunner = run_xcrun) -> str | None:
    """Sign an app bundle again ad hoc, as Xcode signs a simulator's. None when it worked, else what codesign said."""
    result = await xcrun(*resign_argv(app), timeout=RESIGN_TIMEOUT_S, developer_dir=developer_dir)
    return None if result.ok else result.message
