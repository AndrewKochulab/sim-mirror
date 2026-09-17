# SPDX-License-Identifier: Apache-2.0
"""Fail if any file of a Swift package's gated sources is covered less than the minimum.

It reads llvm-cov's JSON export and holds every source file under ``--under`` to the minimum on its own, by lines.

- The native helper: ``swift test --enable-code-coverage`` writes the export. The default ``--under`` is
  ``helper/Sources/HelperCore/``, the part of the helper decided without a simulator; ``HelperPlatform`` reaches Apple's
  private frameworks and a simulator, so it is exercised by the live suite (``make live``) instead.

      uv run python scripts/check_swift_coverage.py --min 98 "$(swift test --package-path helper --show-codecov-path)"

- The app SDK: its tests run on a simulator in two places, the package's own and the sample app's hosted ones, so
  ``make sdk-coverage`` merges both runs' profiles and exports them over both binaries, then checks
  ``sdk/swift/Sources/SimMirrorKit/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The sources held to the minimum when none are named.
GATED = "/helper/Sources/HelperCore/"


def coverage(report: dict[str, Any], under: str = GATED) -> dict[str, float]:
    """Each gated file's line coverage, in percent."""
    found: dict[str, float] = {}
    for export in report.get("data") or []:
        for entry in export.get("files") or []:
            name = str(entry.get("filename", ""))
            if under not in name:
                continue
            lines = entry["summary"]["lines"]
            count = int(lines["count"])
            found[name[name.index(under) + 1 :]] = 100.0 if count == 0 else 100.0 * int(lines["covered"]) / count
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold every gated file of a Swift package to a minimum coverage.")
    parser.add_argument("report", type=Path, help="llvm-cov's JSON export")
    parser.add_argument("--min", type=float, required=True, dest="minimum", help="the minimum percent per file")
    parser.add_argument("--under", default=GATED, help="the sources held to the minimum")
    args = parser.parse_args(argv)
    under = "/" + args.under.strip("/") + "/"
    try:
        files = coverage(json.loads(args.report.read_text(encoding="utf-8")), under)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read the coverage report {args.report}: {exc}", file=sys.stderr)
        return 2
    if not files:
        print(f"the coverage report {args.report} measures no file of {under.strip('/')}", file=sys.stderr)
        return 2
    low = sorted(((name, pct) for name, pct in files.items() if pct < args.minimum), key=lambda item: item[1])
    if not low:
        print(f"swift coverage ok: {len(files)} files of {under.strip('/')} at {args.minimum:g}% or more")
        return 0
    print(f"files of {under.strip('/')} covered less than {args.minimum:g}%:", file=sys.stderr)
    for name, percent in low:
        print(f"  {percent:6.2f}%  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
