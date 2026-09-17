# SPDX-License-Identifier: Apache-2.0
"""Fail if a source file does not say its license.

Every Python, TypeScript, JavaScript, shell, Swift and Objective-C file SimMirror writes starts with an
``SPDX-License-Identifier: Apache-2.0`` comment in its first lines. Generated files that carry another license (the idb
protocol stubs) and built bundles are left out.

Run directly, or via `make lint`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from _repo import REPO_ROOT, read_text, repo_files

HEADER = "SPDX-License-Identifier: Apache-2.0"
SUFFIXES = frozenset({".py", ".ts", ".js", ".mjs", ".sh", ".swift", ".h", ".m"})
#: How far down the header may be: after a shebang, a coding line, a `/// <reference>` or a Swift tools version.
WITHIN_LINES = 5
EXCLUDED_PREFIXES = (
    "src/sim_mirror/connectors/idb/proto/",
    "src/sim_mirror/server/static/viewer/",
    "viewer/dist/",
)


def missing(root: Path, files: Iterable[str]) -> list[str]:
    """The source files whose first lines do not carry the header."""
    found: list[str] = []
    for rel in files:
        if Path(rel).suffix not in SUFFIXES or rel.startswith(EXCLUDED_PREFIXES):
            continue
        text = read_text(root / rel)
        if text is None or not any(HEADER in line for line in text.splitlines()[:WITHIN_LINES]):
            found.append(rel)
    return found


def main(root: Path = REPO_ROOT) -> int:
    found = missing(root, repo_files(root))
    if not found:
        print("license headers ok")
        return 0
    print(f"source files without `{HEADER}` in their first {WITHIN_LINES} lines:\n", file=sys.stderr)
    for rel in found:
        print(f"  {rel}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
