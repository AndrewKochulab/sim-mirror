# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror tools``: the agent tools this install offers, one line each -- or the whole manifest as JSON."""

from __future__ import annotations

import argparse
import json
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.base import Capability
from sim_mirror.daemon.app import SERVER_SCOPE
from sim_mirror.tools.registry import ToolRegistry


def register(commands: Any) -> None:
    command = commands.add_parser(
        "tools", help="list the agent tools, as a connector with every capability offers them"
    )
    command.add_argument("--json", action="store_true", help="print the manifest an MCP client receives")
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    manifest = ToolRegistry().manifest(ctx.config().get(SERVER_SCOPE), frozenset(Capability))
    if args.json:
        ctx.say(json.dumps(manifest, indent=2))
        return 0
    for tool in manifest["tools"]:
        first = tool["description"].split(". ", 1)[0].rstrip(".")
        ctx.say(f"{tool['name']}: {first}.")
    return 0
