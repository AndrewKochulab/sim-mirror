# SPDX-License-Identifier: Apache-2.0
"""The folder each agent named for its builds, kept for its later calls.

An agent working in a git worktree names it once, with a build call's ``folder``; its later builds, test runs, ``sim_app
info`` and relative ``save_to`` paths then use that folder too, without naming it again -- a call that names another
replaces it (`build.provider.working_folder`). It is kept for the agent's connection -- one `Caller.key` in one scope --
in the runtime, not in a host's seams, and forgotten when the connection ends (`Runtime.let_go`).
"""

from __future__ import annotations

from pathlib import Path

from sim_mirror.seams import Caller


class Folders:
    """Each agent connection's folder, by its scope and key."""

    def __init__(self) -> None:
        self._kept: dict[tuple[str, str], Path] = {}

    def of(self, caller: Caller) -> Path | None:
        """The folder this agent last named, or None while it has named none."""
        return self._kept.get((caller.scope.id, caller.key))

    def keep(self, caller: Caller, folder: Path) -> None:
        """This agent's later calls use this folder."""
        self._kept[(caller.scope.id, caller.key)] = folder

    def drop(self, caller: Caller) -> None:
        """Forget this agent's folder: it is no longer one it may use."""
        self._kept.pop((caller.scope.id, caller.key), None)

    def forget(self, key: str) -> None:
        """Forget every folder of an agent whose connection ended."""
        for kept in [kept for kept in self._kept if kept[1] == key]:
            del self._kept[kept]
