# SPDX-License-Identifier: Apache-2.0
"""Fail when the viewer's committed page bundle is not what its source builds, or a bundle is over its size budget.

Run after ``npm run build`` in viewer/ (``make viewer-bundle`` does both). The standalone page in
``src/sim_mirror/server/static/viewer`` is committed, so that an install from a git tag needs no Node -- which only
works while it is exactly what the source builds. And every script a page loads stays within `BUDGET` bytes gzipped.
Standard library only.
"""

from __future__ import annotations

import gzip
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from _repo import REPO_ROOT

BUNDLE_DIR = "src/sim_mirror/server/static/viewer"
#: The most a script a page loads may weigh, gzipped.
BUDGET = 45_000
BUDGETED = ("viewer/dist/index.js", f"{BUNDLE_DIR}/start.js")
REBUILD = "run `make viewer-bundle` and commit the result"

Git = Callable[[Sequence[str]], str]


def git(args: Sequence[str], root: Path = REPO_ROOT) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def gzipped_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def problems(root: Path, run_git: Git) -> list[str]:
    found: list[str] = []
    changed = run_git(["status", "--porcelain", "--untracked-files=all", "--", BUNDLE_DIR])
    for line in changed.splitlines():
        if line.strip():
            found.append(f"{line[3:]}: not what viewer/ builds -- {REBUILD}")
    for rel in BUDGETED:
        path = root / rel
        if not path.is_file():
            found.append(f"{rel}: not built -- {REBUILD}")
            continue
        size = gzipped_size(path)
        if size > BUDGET:
            found.append(f"{rel}: {size / 1000:.1f} KB gzipped, over the {BUDGET // 1000} KB budget")
    return found


def main(root: Path = REPO_ROOT, run_git: Git | None = None) -> int:
    found = problems(root, run_git or (lambda args: git(args, root)))
    if not found:
        sizes = ", ".join(f"{rel} {gzipped_size(root / rel) / 1000:.1f} KB" for rel in BUDGETED)
        print(f"viewer bundle ok: the committed page is what viewer/ builds; gzipped {sizes}")
        return 0
    print("the viewer bundle needs attention:\n", file=sys.stderr)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
