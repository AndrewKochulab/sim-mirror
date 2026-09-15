# SPDX-License-Identifier: Apache-2.0
"""Fail when the pictures in docs/media would make the repository heavy to clone.

Every clone carries every picture ever committed, so each kind has a limit -- a GIF 3 MB, a still image 400 KB -- and
the folder 25 MB in all. Videos are not committed at all: they are attached to a GitHub Release.

Run directly, or via `make lint`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from _repo import REPO_ROOT, repo_files

MEDIA = "docs/media/"
MB = 1_000_000
KB = 1_000
LIMITS = {".gif": 3 * MB, ".png": 400 * KB, ".jpg": 400 * KB, ".jpeg": 400 * KB, ".webp": 400 * KB, ".svg": 400 * KB}
TOTAL = 25 * MB
#: What else the folder may hold: notes on its pictures.
NOTES = frozenset({".md"})


def _size(bytes_: int) -> str:
    return f"{bytes_ / MB:.1f} MB" if bytes_ >= MB else f"{bytes_ // KB} KB"


def problems(root: Path, files: Iterable[str]) -> list[str]:
    found: list[str] = []
    total = 0
    for rel in files:
        if not rel.startswith(MEDIA):
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in NOTES:
            continue
        size = (root / rel).stat().st_size
        total += size
        limit = LIMITS.get(suffix)
        if limit is None:
            found.append(
                f"{rel}: not a picture this folder takes ({', '.join(sorted(LIMITS))}); attach videos to a release"
            )
        elif size > limit:
            found.append(f"{rel}: {_size(size)}, over the {_size(limit)} a {suffix} may be")
    if total > TOTAL:
        found.append(f"{MEDIA}: {_size(total)} in all, over {_size(TOTAL)}")
    return found


def main(root: Path = REPO_ROOT) -> int:
    found = problems(root, repo_files(root))
    if not found:
        print("media ok: every picture in docs/media is within its limit")
        return 0
    print("media too heavy for the repository:\n", file=sys.stderr)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
