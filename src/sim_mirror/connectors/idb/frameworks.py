# SPDX-License-Identifier: Apache-2.0
"""Where Xcode keeps the Simulator frameworks idb_companion loads, so a doctor can say which it would find.

Xcode 27 moved ``SimulatorKit.framework`` from ``Contents/Developer/Library/PrivateFrameworks`` to
``Contents/SharedFrameworks``; tools that looked in one place broke on the other. idb_companion does the loading
itself -- this only reports what is where. ``CoreSimulator.framework`` is the whole machine's, in
``/Library/Developer/PrivateFrameworks``: installing a newer Xcode upgrades it for every Xcode on the Mac.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

SIMULATOR_KIT = "SimulatorKit.framework"
CORE_SIMULATOR = Path("/Library/Developer/PrivateFrameworks/CoreSimulator.framework")


def xcode_contents(developer_dir: Path) -> Path | None:
    """An Xcode's ``Contents`` folder from its developer folder; None for the command-line tools, which have none."""
    if developer_dir.name == "Developer" and developer_dir.parent.name == "Contents":
        return developer_dir.parent
    return None


def simulator_kit(developer_dir: Path) -> Path | None:
    """The SimulatorKit an Xcode has: in SharedFrameworks (Xcode 27 and later), else in PrivateFrameworks."""
    contents = xcode_contents(developer_dir)
    if contents is None:
        return None
    for candidate in (
        contents / "SharedFrameworks" / SIMULATOR_KIT,
        contents / "Developer" / "Library" / "PrivateFrameworks" / SIMULATOR_KIT,
    ):
        if candidate.is_dir():
            return candidate
    return None


def framework_version(framework: Path) -> str | None:
    """A framework's version from its Info.plist: the short version, and the build in parentheses when it has one."""
    for info in (framework / "Resources" / "Info.plist", framework / "Versions" / "A" / "Resources" / "Info.plist"):
        try:
            with info.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        short, build = data.get("CFBundleShortVersionString"), data.get("CFBundleVersion")
        if short or build:
            return f"{short} ({build})" if short and build and short != build else str(short or build)
    return None
