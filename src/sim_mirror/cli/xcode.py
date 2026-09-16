# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror xcode approve``: have Xcode 27 approve SimMirror to use its tools.

Xcode lets an agent use its tools -- the UI hierarchy SimMirror reads through mcpbridge among them -- only once the
agent has opened a project through them, which is when Xcode asks the person, if it is set to ask. Measured on Xcode
27.0 (2026-09-16): the approval is for the program that runs mcpbridge, so it is asked for here by the same Python
the daemon runs; it outlasts Xcode's tool service restarting; and nothing else asks for it. So this command opens the
project named -- or the one in this folder -- through Xcode's tools, and closes it again unless it was open already.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any

from sim_mirror.build.xcodebuild import projects
from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.mcpbridge.client import BridgeError, BridgeRefused
from sim_mirror.connectors.mcpbridge.reader import NO_BRIDGE
from sim_mirror.host_copy import HostCopy

OPEN = "XcodeOpenWorkspace"
LIST = "XcodeListWorkspaces"
CLOSE = "XcodeCloseWorkspace"
#: How long Xcode may take to open the project, which includes a person deciding whether to allow SimMirror.
OPEN_TIMEOUT_S = 300.0
LIST_TIMEOUT_S = 30.0
_OPEN_PATH = re.compile(r"workspacePath: (.+?)\s*$", re.M)


def register(commands: Any) -> None:
    command = commands.add_parser("xcode", help="let SimMirror use Xcode 27's tools")
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    approve = actions.add_parser(
        "approve", help="open a project through Xcode's tools, which is when Xcode approves SimMirror"
    )
    approve.add_argument("project", nargs="?", help="the .xcodeproj or .xcworkspace to open; default: this folder's")
    approve.add_argument("--scope", help="use this scope's Xcode, instead of this folder's project's")
    command.set_defaults(handler=run, action="approve", project=None, scope=None)


def _project(named: str | None, cwd: Path) -> Path | None:
    if named:
        path = (cwd / named).expanduser()
        return path if path.suffix in (".xcodeproj", ".xcworkspace") and path.is_dir() else None
    found = projects(cwd)
    return found[0] if len(found) == 1 else None


def _open_paths(listing: dict[str, Any]) -> set[Path]:
    return {Path(path).resolve() for path in _OPEN_PATH.findall(str(listing.get("message", "")))}


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    return asyncio.run(_approve(args, ctx))


async def _approve(args: argparse.Namespace, ctx: CliContext) -> int:
    copy = HostCopy()
    project = _project(args.project, ctx.cwd)
    if project is None:
        ctx.complain(
            "sim-mirror: name the .xcodeproj or .xcworkspace to open"
            + (f": {args.project} is not one" if args.project else f", since {ctx.cwd} does not have exactly one")
        )
        return 1
    developer_dir = ctx.config().get(ctx.scope(args.scope)).developer_dir
    client = ctx.bridge(developer_dir)
    was_open: set[Path] | None = None
    try:
        # A project a person already has open stays open: only one this command opened is closed again.
        with contextlib.suppress(BridgeRefused):
            was_open = _open_paths(await client.call(LIST, {}, timeout=LIST_TIMEOUT_S))
        opened = await client.call(OPEN, {"path": str(project)}, timeout=OPEN_TIMEOUT_S)
        identifier = opened.get("workspaceIdentifier")
        if isinstance(identifier, str) and was_open is not None and project.resolve() not in was_open:
            with contextlib.suppress(BridgeError):
                await client.call(CLOSE, {"workspaceIdentifier": identifier}, timeout=LIST_TIMEOUT_S)
    except BridgeError as exc:
        missing = NO_BRIDGE in str(exc)
        ctx.complain(f"sim-mirror: {copy.mcpbridge_missing(developer_dir) if missing else exc}")
        return 1
    finally:
        await client.close()
    ctx.say(f"Xcode approved {copy.owner_name} to use its tools, opening {project.name}")
    return 0
