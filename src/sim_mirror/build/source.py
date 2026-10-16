# SPDX-License-Identifier: Apache-2.0
"""Which source a build or a test run built: its folder, and the branch and commit git has it on.

Every answer of `sim_build_run` and `sim_test` carries one such line, and so does the head of its log, so a build of
the wrong folder or branch does not read like the right one -- to the agent, or to a person reading the transcript
later::

    source /Users/dev/NotesProbe · feature/login @ 1a2b3c4 (dirty)

It is read once, as the run starts. ``(dirty)`` is a change git has not committed, an untracked file's included. A
folder git cannot read -- not a checkout, no git, a git too slow to answer (`platform.git`) -- is still named, and the
run goes ahead: the line is for checking, never a reason not to build.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sim_mirror.platform import git, process
from sim_mirror.platform.process import Runner

#: How many characters of a commit are shown: as many as git's own short form starts with.
SHORT = 7


@dataclass(frozen=True)
class Source:
    """What a build built: the line its answer carries, and the commit it names for what is stamped into the app
    (`build.stamp`)."""

    line: str
    #: The commit checked out, in full; None when git could not say, or there is none yet.
    commit: str | None = None
    #: Whether any file differed from that commit.
    dirty: bool = False


#: How a build runner reads a folder's source: `read_source`, or a test's stand-in -- which may answer the line alone.
#: "" says nothing.
SourceReader = Callable[[Path], Awaitable[Source | str]]


def described(folder: Path, checkout: git.Checkout | None) -> str:
    """The source line for a folder whose checkout stands as `checkout`, or is not one git could read."""
    if checkout is None:
        return f"source {folder}"
    branch = checkout.branch or "detached HEAD"
    commit = f" @ {checkout.commit[:SHORT]}" if checkout.commit else ", no commits yet"
    dirty = " (dirty)" if checkout.dirty else ""
    return f"source {folder} · {branch}{commit}{dirty}"


async def read_source(folder: Path, run: Runner = process.run) -> Source:
    """The source of a build of `folder`, as git has it now."""
    checkout = await git.checkout(folder, run)
    line = described(folder, checkout)
    return Source(line) if checkout is None else Source(line, checkout.commit, checkout.dirty)
