# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: SimMirror's tools over stdio for an MCP client, for this project (`mcp.launcher`)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp import launcher


def register(commands: Any) -> None:
    command = commands.add_parser("mcp", help="serve the agent tools over stdio, for an MCP client")
    command.add_argument("--scope", help="the scope to drive, instead of this folder's project")
    command.add_argument(
        "--root", action="append", default=[], help="a folder builds and installs may reach; repeatable (default: here)"
    )
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    roots = [Path(root).expanduser().resolve() for root in args.root] or [ctx.cwd.resolve()]
    return launcher.run(
        ctx.scope(args.scope),
        roots,
        client=ctx.client(),
        start_daemon=ctx.start_daemon,
        env=ctx.env,
        stdin=ctx.stdin,
        stdout=ctx.stdout,
        opener=ctx.opener,
    )
