# SPDX-License-Identifier: Apache-2.0
"""Print a version's section of CHANGELOG.md, as its GitHub Release's notes.

    uv run --no-project python scripts/release_notes.py v0.1.0 > notes.md

A version the changelog has no section for -- or an empty one -- is refused, so a tag pushed before its notes were
written fails the release before anything is published. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from _repo import REPO_ROOT

CHANGELOG = REPO_ROOT / "CHANGELOG.md"
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\].*$", re.MULTILINE)


class NoNotes(LookupError):
    """A version the changelog does not describe."""


def notes(changelog: str, version: str) -> str:
    """The text under ``## [version]``, up to the next version's heading."""
    headings = list(HEADING.finditer(changelog))
    for index, heading in enumerate(headings):
        if heading.group("version") != version:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        body = changelog[heading.end() : end].strip()
        if not body:
            raise NoNotes(f"CHANGELOG.md's section for {version} is empty")
        return body + "\n"
    raise NoNotes(f"CHANGELOG.md has no section for {version}: add `## [{version}] - YYYY-MM-DD` before tagging")


def main(
    argv: Sequence[str] | None = None,
    *,
    changelog: Path = CHANGELOG,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    made = argparse.ArgumentParser(description="Print a version's CHANGELOG.md section.")
    made.add_argument("version", help="the version, with or without a leading v")
    args = made.parse_args(argv)
    version = args.version[1:] if args.version.startswith("v") else args.version
    try:
        text = notes(changelog.read_text(encoding="utf-8"), version)
    except NoNotes as exc:
        print(exc, file=sys.stderr if err is None else err)
        return 1
    (sys.stdout if out is None else out).write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
