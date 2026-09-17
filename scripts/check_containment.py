# SPDX-License-Identifier: Apache-2.0
"""Fail if Xcode's tools, idb_companion or the native helper are named anywhere they could be started from.

SimMirror runs real programs: ``xcrun`` (simctl, xcodebuild, xcresulttool) boots devices, installs apps and builds
whatever project an agent points it at, and ``idb_companion`` and SimMirror's own ``sim-mirror-helper`` stream a
device's screen and take its touches. The promises around them -- an argv and never a shell, a timeout that reaps, off
meaning stopped, no helper left behind -- hold only while one module owns each:

* `sim_mirror/platform/` runs xcrun, names the simctl subcommands, and compiles Swift helpers (`platform.swift`);
* `sim_mirror/connectors/idb/companion.py` finds and starts idb_companion;
* `sim_mirror/connectors/native/helper.py` finds and starts sim-mirror-helper;
* `sim_mirror/build/xcodebuild.py` names the xcodebuild and xcresulttool calls;
* `sim_mirror/connectors/simctl/` is the connector named after simctl;
* `sim_mirror/testing/` names them to refuse them (`guards`) and to play them (`fakes`).

Anywhere else, a string that starts a command with one of them is refused (`_containment` says what starting a
command is). Run directly, or via `make lint`.
"""

from __future__ import annotations

import sys

import _containment
from _repo import REPO_ROOT

PROGRAMS = ("xcrun", "simctl", "xcodebuild", "xcresulttool", "idb_companion", "swiftc", "swift", "sim-mirror-helper")

ALLOWED = (
    "src/sim_mirror/platform/",
    "src/sim_mirror/connectors/idb/companion.py",
    "src/sim_mirror/connectors/native/helper.py",
    "src/sim_mirror/build/xcodebuild.py",
    "src/sim_mirror/connectors/simctl/",
    "src/sim_mirror/testing/",
)

SCAN_DIRS = ("src", "examples", "benchmarks")


def offenders() -> list[tuple[str, int, str]]:
    return _containment.offenders(REPO_ROOT, SCAN_DIRS, ALLOWED, PROGRAMS)


def main() -> int:
    found = offenders()
    if not found:
        print("containment ok: Xcode's tools and the device helpers are named only by the modules that run them")
        return 0
    print("an Xcode tool or a device helper is named outside the module that runs it:\n", file=sys.stderr)
    for rel, lineno, program in found:
        print(f"  {rel}:{lineno}: {program}", file=sys.stderr)
    print(
        "\nRun xcrun, simctl and swiftc through sim_mirror/platform/, start idb_companion only through "
        "sim_mirror/connectors/idb/companion.py and sim-mirror-helper only through "
        "sim_mirror/connectors/native/helper.py, and build through sim_mirror/build/xcodebuild.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
