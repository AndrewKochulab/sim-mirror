# SPDX-License-Identifier: Apache-2.0
"""Whether a daemon is running, and on which port: ``daemon.json`` in the run folder.

The daemon writes it once it listens -- its pid, port and version, private to its owner -- and removes it when it stops,
if the file is still its own. A CLI command reads it to find the daemon; a file whose process is gone is stale and read
as no daemon. ``sim-mirror serve --detach`` starts one in a process group of its own, its output in the log folder.

``stopped`` beside it says a person stopped the daemon on purpose (``sim-mirror stop --daemon``), with a mark of its
own each time, until a daemon starts again: an MCP relay still running tells its client so once, rather than starting
it again at once (`mcp.launcher.reviver`).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sim_mirror.platform import process
from sim_mirror.storage.private import write_private

INFO_FILE = "daemon.json"
STOPPED_FILE = "stopped"
#: Held while a daemon is started, so two clients starting one at once start only one.
START_LOCK_FILE = "daemon-start.lock"
LOOPBACK = "127.0.0.1"

Spawner = Callable[[Sequence[str], Path], Awaitable[Any]]


@dataclass(frozen=True)
class DaemonInfo:
    pid: int
    port: int
    version: str

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"


def info_path(run_dir: Path) -> Path:
    return run_dir / INFO_FILE


def write_info(run_dir: Path, info: DaemonInfo) -> None:
    write_private(info_path(run_dir), (json.dumps(asdict(info)) + "\n").encode("utf-8"))


def read_info(run_dir: Path, *, alive: Callable[[int], bool] = process.pid_alive) -> DaemonInfo | None:
    """The running daemon's pid, port and version -- or None when there is none, or only a stale file."""
    try:
        document = json.loads(info_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    pid, port, version = document.get("pid"), document.get("port"), document.get("version")
    numbers = all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (pid, port))
    if not numbers or not isinstance(version, str):
        return None
    info = DaemonInfo(int(pid), int(port), version)  # type: ignore[arg-type]
    return info if alive(info.pid) else None


def remove_info(run_dir: Path, pid: int) -> bool:
    """Remove the file if it names this process. Answers whether it did."""
    path = info_path(run_dir)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(document, dict) or document.get("pid") != pid:
        return False
    path.unlink(missing_ok=True)
    return True


def mark_stopped(run_dir: Path) -> str:
    """Write down that a person is stopping the daemon on purpose. Answers this stop's own mark."""
    mark = secrets.token_hex(8)
    write_private(run_dir / STOPPED_FILE, (mark + "\n").encode("ascii"))
    return mark


def stopped_mark(run_dir: Path) -> str | None:
    """The mark of the stop a person asked for since a daemon last started, or None."""
    try:
        return (run_dir / STOPPED_FILE).read_text(encoding="ascii").strip() or None
    except (OSError, ValueError):
        return None


def clear_stopped(run_dir: Path) -> None:
    """A daemon is running again, or was not stopped after all."""
    (run_dir / STOPPED_FILE).unlink(missing_ok=True)


async def start_detached(argv: Sequence[str], log_path: Path, *, spawn: Spawner = process.spawn) -> int:
    """Start the daemon in a process group of its own, its output appended to `log_path`. Answers its pid."""
    started = await spawn(argv, log_path)
    return int(started.pid)
