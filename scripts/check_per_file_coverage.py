# SPDX-License-Identifier: Apache-2.0
"""Fail if any one file is covered less than the minimum, whatever the total says.

A total of 99% can hide a new module at 60% behind a large one at 100%. This reads the JSON report pytest-cov writes
(``--cov-report=json``) and holds every measured file -- lines and branches together, as coverage.py counts them -- to
the minimum on its own.

    uv run python scripts/check_per_file_coverage.py --min 98 coverage.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def shortfalls(report: dict[str, Any], minimum: float) -> list[tuple[str, float]]:
    """Each (file, percent) covered less than `minimum`, the least covered first."""
    files = report.get("files") or {}
    low = [(name, float(data["summary"]["percent_covered"])) for name, data in files.items()]
    return sorted(((name, percent) for name, percent in low if percent < minimum), key=lambda item: item[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold every file to a minimum coverage.")
    parser.add_argument("report", type=Path, help="coverage.py's JSON report")
    parser.add_argument("--min", type=float, required=True, dest="minimum", help="the minimum percent per file")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read the coverage report {args.report}: {exc}", file=sys.stderr)
        return 2
    low = shortfalls(report, args.minimum)
    if not low:
        print(f"per-file coverage ok: {len(report.get('files') or {})} files at {args.minimum:g}% or more")
        return 0
    print(f"files covered less than {args.minimum:g}%:\n", file=sys.stderr)
    for name, percent in low:
        print(f"  {percent:6.2f}%  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
