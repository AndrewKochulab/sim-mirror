# SPDX-License-Identifier: Apache-2.0
"""Fail if any file of the native helper's core is covered less than the minimum.

``swift test --enable-code-coverage`` writes llvm-cov's JSON export; this holds every source file under
``helper/Sources/HelperCore/`` -- the part of the helper that is decided without a simulator -- to the minimum on its
own, by lines. ``HelperPlatform`` reaches Apple's private frameworks and a simulator, so it is exercised by the live
suite (``make live``) rather than counted here.

    uv run python scripts/check_swift_coverage.py --min 98 "$(swift test --package-path helper --show-codecov-path)"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The sources held to the minimum.
GATED = "/helper/Sources/HelperCore/"


def coverage(report: dict[str, Any]) -> dict[str, float]:
    """Each gated file's line coverage, in percent."""
    found: dict[str, float] = {}
    for export in report.get("data") or []:
        for entry in export.get("files") or []:
            name = str(entry.get("filename", ""))
            if GATED not in name:
                continue
            lines = entry["summary"]["lines"]
            count = int(lines["count"])
            found[name[name.index(GATED) + 1 :]] = 100.0 if count == 0 else 100.0 * int(lines["covered"]) / count
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold every file of the helper's core to a minimum coverage.")
    parser.add_argument("report", type=Path, help="llvm-cov's JSON export from swift test")
    parser.add_argument("--min", type=float, required=True, dest="minimum", help="the minimum percent per file")
    args = parser.parse_args(argv)
    try:
        files = coverage(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read the coverage report {args.report}: {exc}", file=sys.stderr)
        return 2
    if not files:
        print(f"the coverage report {args.report} measures no file of {GATED.strip('/')}", file=sys.stderr)
        return 2
    low = sorted(((name, pct) for name, pct in files.items() if pct < args.minimum), key=lambda item: item[1])
    if not low:
        print(f"helper coverage ok: {len(files)} files at {args.minimum:g}% or more")
        return 0
    print(f"helper files covered less than {args.minimum:g}%:", file=sys.stderr)
    for name, percent in low:
        print(f"  {percent:6.2f}%  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
