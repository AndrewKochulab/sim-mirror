# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror devices``: this Mac's iOS simulators, and which one a project uses."""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp.launcher import ensure_daemon

SCOPE_HELP = "for this scope, instead of this folder's project"


def register(commands: Any) -> None:
    command = commands.add_parser("devices", help="list this Mac's iOS simulators, or choose one for a project")
    command.add_argument("--scope", help=SCOPE_HELP)
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    listing = actions.add_parser("list", help="list the simulators a project could use (the default)")
    choosing = actions.add_parser("choose", help="use this simulator for a project from now on")
    choosing.add_argument("udid")
    for action in (listing, choosing):
        # Also taken after the action; left unset when it is not given there, so one given before the action stands.
        action.add_argument("--scope", default=argparse.SUPPRESS, help=SCOPE_HELP)
    command.set_defaults(handler=run, action="list")


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    base = f"/api/v1/scopes/{scope.id}"
    if args.action == "choose":
        client.put(f"{base}/device", {"udid": args.udid})
        ctx.say(f"{scope.label} uses {args.udid} from now on")
        return 0
    listed = client.get(f"{base}/devices")["devices"]
    if not listed:
        ctx.say("no iOS simulators are available on this Mac")
    for device in listed:
        made = "  (made by SimMirror)" if device["created"] else ""
        ctx.say(f"{device['udid']}  {device['runtime']}  {device['name']}  {device['state']}{made}")
    return 0
