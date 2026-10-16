# SPDX-License-Identifier: Apache-2.0
"""Which commit a folder's git checkout is on, and whether it has changes not committed -- read, never written.

One call, ``git status --porcelain=v2 --branch``, answers the branch, the commit and every changed or untracked file,
in a format git keeps stable for programs. ``--no-optional-locks`` keeps it from refreshing the index, so it never
takes a lock a person's own git command could then trip on. It is a short query (`process.run`): it never raises, and
one that does not answer within its timeout is ended. A folder git cannot read -- not a checkout, no git on this Mac, a
git that took too long -- answers None, and the caller carries on without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sim_mirror.platform import process
from sim_mirror.platform.process import Runner

#: What porcelain v2 says for a branch with no commits yet, and for a checkout on no branch.
_INITIAL = "(initial)"
_DETACHED = "(detached)"


@dataclass(frozen=True)
class Checkout:
    """Where a git checkout stands."""

    #: The branch checked out; None for a detached HEAD.
    branch: str | None
    #: The commit checked out, in full; None on a branch with no commits yet.
    commit: str | None
    #: Whether any file differs from that commit, untracked ones included.
    dirty: bool


def status_argv(folder: Path) -> tuple[str, ...]:
    return ("git", "--no-optional-locks", "-C", str(folder), "status", "--porcelain=v2", "--branch")


def parse_status(out: str) -> Checkout | None:
    """A checkout from what ``git status --porcelain=v2 --branch`` printed; None when it named no commit line."""
    headers: dict[str, str] = {}
    dirty = False
    for line in out.splitlines():
        if line.startswith("# "):
            key, _, value = line[2:].partition(" ")
            headers[key] = value.strip()
        elif line.strip():
            dirty = True
    oid, head = headers.get("branch.oid"), headers.get("branch.head")
    if not oid or not head:
        return None
    return Checkout(
        branch=None if head == _DETACHED else head,
        commit=None if oid == _INITIAL else oid,
        dirty=dirty,
    )


async def checkout(folder: Path, run: Runner = process.run) -> Checkout | None:
    """Where the git checkout holding `folder` stands, or None when git cannot say."""
    code, out = await run(status_argv(folder))
    return parse_status(out) if code == 0 else None
