# SPDX-License-Identifier: Apache-2.0
"""The source line of a build's answer and log: the folder always, its branch and commit when git can say."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sim_mirror.build.source import Source, described, read_source
from sim_mirror.platform.git import Checkout

FOLDER = Path("/Users/dev/NotesProbe-login")
COMMIT = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def test_the_line_names_the_folder_branch_and_short_commit_and_says_when_there_are_uncommitted_changes() -> None:
    assert described(FOLDER, Checkout("feature/login", COMMIT, False)) == (
        "source /Users/dev/NotesProbe-login · feature/login @ 1a2b3c4"
    )
    assert described(FOLDER, Checkout("feature/login", COMMIT, True)) == (
        "source /Users/dev/NotesProbe-login · feature/login @ 1a2b3c4 (dirty)"
    )


def test_a_detached_head_and_a_branch_with_no_commits_are_said_as_such() -> None:
    assert described(FOLDER, Checkout(None, COMMIT, False)) == (
        "source /Users/dev/NotesProbe-login · detached HEAD @ 1a2b3c4"
    )
    assert described(FOLDER, Checkout("main", None, True)) == (
        "source /Users/dev/NotesProbe-login · main, no commits yet (dirty)"
    )


async def test_a_folder_git_cannot_read_is_still_named() -> None:
    asked: list[Sequence[str]] = []

    async def not_a_checkout(argv: Sequence[str]) -> tuple[int, str]:
        asked.append(argv)
        return 128, ""

    assert await read_source(FOLDER, not_a_checkout) == Source("source /Users/dev/NotesProbe-login")
    assert asked and asked[0][:4] == ("git", "--no-optional-locks", "-C", str(FOLDER))

    async def on_a_branch(argv: Sequence[str]) -> tuple[int, str]:
        return 0, f"# branch.oid {COMMIT}\n# branch.head main\n? New.swift\n"

    # With the whole commit, and whether files differ from it, for what is stamped into the app.
    assert await read_source(FOLDER, on_a_branch) == Source(
        "source /Users/dev/NotesProbe-login · main @ 1a2b3c4 (dirty)", COMMIT, dirty=True
    )
