# SPDX-License-Identifier: Apache-2.0
"""Fail if any file of a Swift package's gated sources is covered less than the minimum.

It reads either of the two coverage formats SimMirror's Swift code is measured in, and holds every source file under
``--under`` to the minimum on its own, by lines:

- llvm-cov's JSON export, which ``swift test --enable-code-coverage`` writes for the native helper. Its default
  ``--under`` is ``helper/Sources/HelperCore/``, the part of the helper decided without a simulator; ``HelperPlatform``
  reaches Apple's private frameworks and a simulator, so it is exercised by the live suite (``make live``) instead.

      uv run python scripts/check_swift_coverage.py --min 98 "$(swift test --package-path helper --show-codecov-path)"

- ``xccov view --archive --json`` of one or more xcresult bundles, for the app SDK, whose tests run on a simulator in
  two places: the package's own tests and the sample app's hosted ones. A line counts as covered when some run executed
  it and every part of it was executed by at least one run -- a ``||`` whose right side no run reached is not.

      uv run python scripts/check_swift_coverage.py --min 98 --under sdk/swift/Sources/SimMirrorKit/ a.json b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The sources held to the minimum when none are named.
GATED = "/helper/Sources/HelperCore/"


def _gated(name: str, under: str) -> str | None:
    """The file's path from `under` on, or None when it is not under it."""
    return name[name.index(under) + 1 :] if under in name else None


def coverage(report: dict[str, Any], under: str = GATED) -> dict[str, float]:
    """Each gated file's line coverage in an llvm-cov export, in percent."""
    found: dict[str, float] = {}
    for export in report.get("data") or []:
        for entry in export.get("files") or []:
            name = _gated(str(entry.get("filename", "")), under)
            if name is None:
                continue
            lines = entry["summary"]["lines"]
            count = int(lines["count"])
            found[name] = 100.0 if count == 0 else 100.0 * int(lines["covered"]) / count
    return found


def xccov_coverage(archives: list[dict[str, Any]], under: str) -> dict[str, float]:
    """Each gated file's line coverage across xccov archives, in percent: a line is covered when some archive ran it
    and no part of it went unrun in every archive that ran it."""
    runs: dict[str, dict[int, list[set[int]]]] = {}
    for archive in archives:
        for path, lines in archive.items():
            name = _gated(str(path), under)
            if name is None:
                continue
            file = runs.setdefault(name, {})
            for line in lines:
                if not line.get("isExecutable"):
                    continue
                ran = file.setdefault(int(line["line"]), [])
                if int(line.get("executionCount") or 0) > 0:
                    ran.append(
                        {int(part["column"]) for part in line.get("subranges") or [] if not part["executionCount"]}
                    )
    found: dict[str, float] = {}
    for name, lines in runs.items():
        covered = sum(1 for unrun in lines.values() if unrun and not set.intersection(*unrun))
        found[name] = 100.0 if not lines else 100.0 * covered / len(lines)
    return found


def measure(reports: list[Any], under: str) -> dict[str, float]:
    if len(reports) == 1 and isinstance(reports[0], dict) and "data" in reports[0]:
        return coverage(reports[0], under)
    if not all(
        isinstance(report, dict) and all(isinstance(lines, list) for lines in report.values()) for report in reports
    ):
        raise ValueError("a report is neither llvm-cov's export nor xccov's archive")
    return xccov_coverage(reports, under)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold every gated file of a Swift package to a minimum coverage.")
    parser.add_argument("reports", type=Path, nargs="+", help="llvm-cov's JSON export, or xccov archives as JSON")
    parser.add_argument("--min", type=float, required=True, dest="minimum", help="the minimum percent per file")
    parser.add_argument("--under", default=GATED, help="the sources held to the minimum")
    args = parser.parse_args(argv)
    under = "/" + args.under.strip("/") + "/"
    try:
        files = measure([json.loads(report.read_text(encoding="utf-8")) for report in args.reports], under)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read the coverage report: {exc}", file=sys.stderr)
        return 2
    if not files:
        print(f"the coverage report measures no file of {under.strip('/')}", file=sys.stderr)
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
