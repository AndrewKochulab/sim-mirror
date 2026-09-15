# SPDX-License-Identifier: Apache-2.0
"""What a test suite installs so that no test reaches a real Simulator, Xcode or AI agent.

Two promises, each one call:

* `install_subprocess_guard` refuses any subprocess that would run Xcode's tools, idb_companion, an AI agent's CLI or
  a macOS app opener -- in first position, handed to a wrapper such as ``env``, or as the first word of a shell
  command. A real one boots a device, builds an app or costs money, and a fake plays every one of them.
* `isolate_state` points SimMirror's state, sockets, logs, device claims and configuration at a temporary folder,
  so a test never reads or reaps what a SimMirror running on the same Mac owns.

They are shipped rather than kept in this repository's tests so that a host embedding SimMirror can make its own suite
keep the same promises.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path, PurePath
from typing import Any, Protocol

from sim_mirror.config.discovery import CONFIG_ENV
from sim_mirror.storage.app_support import CLAIMS_DIR_ENV, LOG_DIR_ENV, RUN_DIR_ENV, STATE_DIR_ENV

#: Programs no test may start.
FORBIDDEN_PROGRAMS = frozenset({"xcrun", "xcodebuild", "idb_companion", "claude", "osascript", "open"})

#: The environment variables that move SimMirror's folders, and the folder each gets under a test's root.
STATE_FOLDERS = {
    STATE_DIR_ENV: "state",
    RUN_DIR_ENV: "run",
    LOG_DIR_ENV: "logs",
    CLAIMS_DIR_ENV: "claims",
}


class RefusedSubprocess(AssertionError):
    """A test tried to start a program no test may start."""


class Patcher(Protocol):
    """The part of pytest's `MonkeyPatch` the guards use."""

    def setattr(self, target: Any, name: str, value: Any) -> None: ...

    def setenv(self, name: str, value: str) -> None: ...


def _program(word: object) -> str:
    return PurePath(str(word)).name


def forbidden(argv: Iterable[object], programs: frozenset[str] = FORBIDDEN_PROGRAMS) -> str | None:
    """The forbidden program an argv would run, if any.

    Every argument counts, not only the first: ``env DEVELOPER_DIR=… xcrun simctl`` and ``tmux new -- claude`` run
    the program from a later position. An argument with spaces in it is a command line for a shell, so its first
    word counts too.
    """
    for arg in argv:
        if isinstance(arg, (list, tuple)):
            nested = forbidden(arg, programs)
            if nested is not None:
                return nested
            continue
        words = str(arg).split()
        for candidate in (str(arg), *words[:1]):
            if _program(candidate) in programs:
                return _program(candidate)
    return None


def _refuse(how: str, argv: Iterable[object], programs: frozenset[str]) -> None:
    listed = list(argv)
    program = forbidden(listed, programs)
    if program is not None:
        raise RefusedSubprocess(
            f"{how} tried to run {program!r}. Tests never start Xcode's tools, idb_companion, an AI agent or an app "
            f"opener: use the fakes in sim_mirror.testing, or mark a test of the process layer "
            f"@pytest.mark.allow_subprocess and run a stand-in binary. argv={listed!r}"
        )


def _argv_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[object]:
    first = args[0] if args else kwargs.get("args")
    if isinstance(first, (str, bytes, PurePath)):
        return [first.decode() if isinstance(first, bytes) else first]
    return list(first or [])


def install_subprocess_guard(patcher: Patcher, programs: frozenset[str] = FORBIDDEN_PROGRAMS) -> None:
    """Refuse every way this process starts a subprocess, when it would run one of `programs`."""
    real_exec = asyncio.create_subprocess_exec
    real_shell = asyncio.create_subprocess_shell
    real_popen = subprocess.Popen
    real_run = subprocess.run

    async def create_subprocess_exec(*args: Any, **kwargs: Any) -> Any:
        _refuse("create_subprocess_exec", args, programs)
        return await real_exec(*args, **kwargs)

    async def create_subprocess_shell(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        _refuse("create_subprocess_shell", [cmd], programs)
        return await real_shell(cmd, *args, **kwargs)

    def wrap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            _refuse(name, _argv_of(args, kwargs), programs)
            return original(*args, **kwargs)

        return guarded

    patcher.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    patcher.setattr(asyncio, "create_subprocess_shell", create_subprocess_shell)
    patcher.setattr(subprocess, "Popen", wrap("Popen", real_popen))
    patcher.setattr(subprocess, "run", wrap("run", real_run))


def isolate_state(patcher: Patcher, root: Path) -> dict[str, Path]:
    """Point SimMirror's folders and configuration file under `root`. Answers each variable's new value."""
    moved = {name: root / folder for name, folder in STATE_FOLDERS.items()}
    moved[CONFIG_ENV] = root / "config.toml"
    for name, path in moved.items():
        patcher.setenv(name, str(path))
    return moved
