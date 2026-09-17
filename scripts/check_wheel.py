# SPDX-License-Identifier: Apache-2.0
"""Fail when a wheel does not carry the native helper the way SimMirror looks for it.

Every wheel carries the helper's Swift package at ``sim_mirror/_helper_src``, so ``sim-mirror helper build`` works from
any install. A release wheel (``--helper``) also carries the helper itself at ``sim_mirror/_bin/sim-mirror-helper``:
executable, a universal Mach-O for both Mac architectures, and tagged for macOS. Standard library only.

    uv run python scripts/check_wheel.py --helper dist/python/sim_mirror-1.0.0-py3-none-macosx_14_0_universal2.whl
"""

from __future__ import annotations

import argparse
import stat
import sys
import zipfile
from pathlib import Path

from hatch_build import BINARY_TARGET, MAC_TAG, SOURCES_TARGET

SOURCES = (f"{SOURCES_TARGET}/Package.swift", f"{SOURCES_TARGET}/Sources/sim-mirror-helper/main.swift")
#: The first bytes of a universal ("fat") Mach-O, which holds a binary for more than one architecture.
UNIVERSAL_MAGIC = bytes.fromhex("cafebabe")


def wheel_tags(wheel: zipfile.ZipFile) -> list[str]:
    """The tags the wheel's WHEEL metadata declares."""
    metadata = next((name for name in wheel.namelist() if name.endswith(".dist-info/WHEEL")), None)
    if metadata is None:
        return []
    lines = wheel.read(metadata).decode("utf-8").splitlines()
    return [line.partition(":")[2].strip() for line in lines if line.startswith("Tag:")]


def problems(path: Path, *, helper: bool) -> list[str]:
    """What is wrong with the wheel at `path`; `helper` for a release wheel, which carries the built helper."""
    try:
        wheel = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"{path}: not a wheel ({exc})"]
    with wheel:
        names = set(wheel.namelist())
        found = [f"{path.name}: the helper's sources are missing {name}" for name in SOURCES if name not in names]
        if not helper:
            return found
        if BINARY_TARGET not in names:
            return [*found, f"{path.name}: carries no helper at {BINARY_TARGET}"]
        info = wheel.getinfo(BINARY_TARGET)
        if not (info.external_attr >> 16) & stat.S_IXUSR:
            found.append(f"{path.name}: {BINARY_TARGET} is not executable")
        with wheel.open(info) as binary:
            if binary.read(len(UNIVERSAL_MAGIC)) != UNIVERSAL_MAGIC:
                found.append(f"{path.name}: {BINARY_TARGET} is not a universal binary for both Mac architectures")
        if wheel_tags(wheel) != [MAC_TAG]:
            found.append(f"{path.name}: is tagged {wheel_tags(wheel)}, not [{MAC_TAG!r}]")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that a wheel carries the native helper.")
    parser.add_argument("wheels", nargs="+", type=Path, help="the wheels to check")
    parser.add_argument("--helper", action="store_true", help="a release wheel, which carries the built helper")
    args = parser.parse_args(argv)
    found = [problem for wheel in args.wheels for problem in problems(wheel, helper=args.helper)]
    if not found:
        carried = "the helper and its sources" if args.helper else "the helper's sources"
        print(f"wheel ok: {len(args.wheels)} wheel(s) carry {carried}")
        return 0
    print("a wheel does not carry the native helper as SimMirror looks for it:\n", file=sys.stderr)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
