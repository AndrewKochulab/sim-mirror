# SPDX-License-Identifier: Apache-2.0
"""What the repository's checks share: its root, and the files it holds."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def repo_files(root: Path = REPO_ROOT) -> list[str]:
    """Every file git tracks or would track (untracked but not ignored), as repository-relative POSIX paths."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = {name for name in listed.stdout.decode("utf-8").split("\0") if name}
    return sorted(name for name in names if (root / name).is_file())


def read_text(path: Path) -> str | None:
    """A file's text, or None for a binary file."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
