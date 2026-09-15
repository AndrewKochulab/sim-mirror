# SPDX-License-Identifier: Apache-2.0
"""Fail if SimMirror's files use a host application's vocabulary instead of its own.

SimMirror is embedded by host applications, and it knows none of them: a host's projects, workspaces or sessions reach
it as an opaque `Scope`, and its wording is the host's to supply (`HostCopy`). A host's own words creeping into the
code, the docs or a message are how that boundary erodes, so they are refused everywhere except here and in this
check's test.

Run directly, or via `make lint`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

from _repo import REPO_ROOT, read_text, repo_files

FORBIDDEN = (
    re.compile(r"dashboard", re.IGNORECASE),
    re.compile(r"tracker_dir", re.IGNORECASE),
    re.compile(r"\.tracker\b", re.IGNORECASE),
    re.compile(r"topic_id", re.IGNORECASE),
    re.compile(r"workspace_id", re.IGNORECASE),
    re.compile(r"\bseats?\b", re.IGNORECASE),
    re.compile(r"hook-secret", re.IGNORECASE),
)

#: Files that must name the words: this check, and its test.
EXEMPT = frozenset({"scripts/check_host_neutral.py", "tests/scripts/test_check_host_neutral.py"})
#: Lock files list other projects' packages, which SimMirror does not write.
SKIPPED = frozenset({"uv.lock", "viewer/package-lock.json"})


def offenders(root: Path, files: Iterable[str]) -> list[tuple[str, int, str]]:
    """Every (file, line, word) using a forbidden word."""
    found: list[tuple[str, int, str]] = []
    for rel in files:
        if rel in EXEMPT or rel in SKIPPED:
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in FORBIDDEN:
                match = pattern.search(line)
                if match:
                    found.append((rel, lineno, match.group(0)))
    return found


def main(root: Path = REPO_ROOT) -> int:
    found = offenders(root, repo_files(root))
    if not found:
        print("host-neutral ok: no host application's vocabulary")
        return 0
    print("a host application's vocabulary is used:\n", file=sys.stderr)
    for rel, lineno, word in found:
        print(f"  {rel}:{lineno}: {word}", file=sys.stderr)
    print(
        "\nSimMirror knows scopes, clients and agents, not a host's concepts. Use Scope for what a host groups devices "
        "by, and HostCopy for wording a host supplies.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
