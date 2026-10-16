# SPDX-License-Identifier: Apache-2.0
"""Where a folder's git checkout stands: one read-only status call, and None whenever git cannot say."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror.platform import process
from sim_mirror.platform.git import Checkout, checkout, parse_status

COMMIT = "5c26caa6e037d40155a39a9f95fe468a0a676595"
CLEAN = (
    f"# branch.oid {COMMIT}\n# branch.head feature/login\n# branch.upstream origin/feature/login\n# branch.ab +0 -0\n"
)


class Git:
    """A stand-in for git that answers one status and remembers being asked."""

    def __init__(self, code: int = 0, out: str = CLEAN) -> None:
        self.code, self.out = code, out
        self.asked: list[tuple[str, ...]] = []

    async def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return self.code, self.out


async def test_a_checkout_is_read_with_one_status_call_that_takes_no_lock(tmp_path: Path) -> None:
    git = Git()
    assert await checkout(tmp_path, git) == Checkout("feature/login", COMMIT, dirty=False)
    assert git.asked == [
        ("git", "--no-optional-locks", "-C", str(tmp_path), "status", "--porcelain=v2", "--branch"),
    ]


@pytest.mark.parametrize(
    ("entry", "dirty"),
    [
        ("1 .M N... 100644 100644 100644 abc abc Sources/App.swift", True),
        ("2 R. N... 100644 100644 100644 abc abc R100 New.swift\tOld.swift", True),
        ("u UU N... 100644 100644 100644 100644 abc abc abc Conflict.swift", True),
        ("? Untracked.swift", True),
        ("", False),
    ],
)
def test_any_changed_or_untracked_file_makes_a_checkout_dirty(entry: str, dirty: bool) -> None:
    assert parse_status(f"{CLEAN}{entry}\n") == Checkout("feature/login", COMMIT, dirty=dirty)


def test_a_detached_head_has_no_branch_and_a_new_branch_has_no_commit() -> None:
    assert parse_status(f"# branch.oid {COMMIT}\n# branch.head (detached)\n") == Checkout(None, COMMIT, False)
    assert parse_status("# branch.oid (initial)\n# branch.head main\n? README.md\n") == Checkout("main", None, True)


@pytest.mark.parametrize("out", ["", "# branch.head main\n", f"# branch.oid {COMMIT}\n", "garbage\n"])
def test_a_status_without_its_branch_lines_is_no_checkout(out: str) -> None:
    assert parse_status(out) is None


@pytest.mark.parametrize("code", [128, process.CANNOT_RUN, process.TIMED_OUT])
async def test_a_folder_git_cannot_read_in_time_is_no_checkout(tmp_path: Path, code: int) -> None:
    # 128: not a checkout, or one git will not read; then no git on this Mac, and a git that took too long.
    assert await checkout(tmp_path, Git(code, CLEAN if code == process.TIMED_OUT else "")) is None
