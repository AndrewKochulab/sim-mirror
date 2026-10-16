# SPDX-License-Identifier: Apache-2.0
"""The doctor's worktree check: a git worktree whose scope has no settings of its own, while its main checkout's has.

A standalone scope is its folder (`Scope.for_folder`), so a git worktree of a project -- a second checkout, on another
branch -- is a scope of its own, and the ``[scopes."<id>"]`` table a person filled in for the project does not reach it:
`sim-mirror mcp` started in the worktree runs without those settings (``build.tools`` off, say), and nothing says why.
This check knows a worktree the way builds do (`build.checkouts`, from its files, with no git run), finds the same
folder in the main checkout, and compares the two scopes' own tables in config.toml.

It changes nothing. When the worktree's scope has none of the settings its main checkout's has, it says so and gives
the command that copies them, `sim-mirror config copy`, for a person to run.
"""

from __future__ import annotations

from pathlib import Path

from sim_mirror.build.checkouts import checkout_of, main_of
from sim_mirror.config import schema
from sim_mirror.config.writer import ConfigError, ConfigWriter
from sim_mirror.doctor.report import CheckResult
from sim_mirror.scope import Scope

NAME = "worktree settings"
#: How many of the main checkout's settings the warning names before it counts the rest.
NAMED_MAX = 6


def _own(writer: ConfigWriter, scope: Scope) -> list[str]:
    """The settings a scope's own table sets that a scope may have, by path, in the reference's order."""
    values = writer.values(scope=scope.id)
    return [setting.path for setting in schema.SETTINGS if setting.key in values and setting.reach == "scope"]


def _named(paths: list[str]) -> str:
    more = len(paths) - NAMED_MAX
    return ", ".join(paths[:NAMED_MAX]) + (f" and {more} more" if more > 0 else "")


def copy_command(source: Scope, target: Scope) -> str:
    """The command that copies one scope's own settings into another's table."""
    return f"sim-mirror config copy --from {source.id} --scope {target.id}"


def check_worktree_settings(folder: Path | None, config_file: Path | None) -> CheckResult:
    """Whether the folder SimMirror runs in is a git worktree whose scope lacks the settings its main checkout's has."""
    if folder is None or config_file is None:
        return CheckResult(NAME, "skip", "no project folder to look at")
    checkout = checkout_of(folder)
    if checkout is None:
        return CheckResult(NAME, "skip", f"{folder} is not in a git checkout")
    main = main_of(checkout)
    if main is None:
        return CheckResult(NAME, "skip", f"{checkout.top} belongs to a repository with no main checkout")
    if main.top == checkout.top:
        return CheckResult(NAME, "skip", f"{folder} is in the main checkout of its repository, not a git worktree")
    here = Scope.for_folder(folder)
    twin = main.top / folder.resolve().relative_to(checkout.top)
    there = Scope.for_folder(twin)
    writer = ConfigWriter(config_file)
    try:
        own, theirs = _own(writer, here), _own(writer, there)
    except ConfigError as exc:
        return CheckResult(NAME, "skip", str(exc), "`sim-mirror config validate` says what is wrong with it")
    worktree = f"{folder} is a git worktree of {main.top}"
    if own:
        return CheckResult(NAME, "ok", f"{worktree}, and its scope {here.id} has settings of its own")
    if not theirs:
        return CheckResult(
            NAME, "ok", f"{worktree}; neither it nor {twin} has settings of its own, so both use the file's"
        )
    return CheckResult(
        NAME,
        "warn",
        f"{worktree}, whose scope {there.id} sets {_named(theirs)}; this folder's scope {here.id} sets none of them, "
        "so SimMirror started here runs without them",
        f"`{copy_command(there, here)}` copies them",
    )
