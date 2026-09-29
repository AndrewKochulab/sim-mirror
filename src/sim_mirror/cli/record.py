# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror record``: recording a project's device, as the viewer's Record button and an agent's `sim_record` do.

``start`` begins -- as ``recording.format`` says, unless ``--format`` says otherwise -- ``stop`` keeps it and says where
each file is, and ``list`` names the recordings kept. The daemon records; this asks it to.
"""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.mcp.launcher import ensure_daemon
from sim_mirror.tools.record import describe


def register(commands: Any) -> None:
    command = commands.add_parser("record", help="record a project's device as an MP4 or a GIF")
    command.add_argument("--scope", help=SCOPE_HELP)
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    start = actions.add_parser("start", help="start recording the device")
    start.add_argument("--format", choices=("mp4", "gif", "both"), help="what to keep, instead of recording.format")
    actions.add_parser("stop", help="stop recording and keep it")
    actions.add_parser("list", help="name the recordings kept (the default)")
    for action in actions.choices.values():
        action.add_argument("--scope", default=argparse.SUPPRESS, help=SCOPE_HELP)
    command.set_defaults(handler=run, action="list", format=None)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    base = f"/api/v1/scopes/{scope.id}"
    if args.action == "start":
        started = client.admin("POST", f"{base}/recording", {"format": args.format})["recording"]
        ctx.say(f"recording {scope.label} (by {started['by']}); `sim-mirror record stop` keeps it")
    elif args.action == "stop":
        ctx.say("kept " + describe(client.admin("DELETE", f"{base}/recording")["recording"]))
    else:
        kept = client.get(f"{base}/recordings")["recordings"]
        ctx.say("\n\n".join(describe(recording) for recording in kept) if kept else "no recordings kept yet")
    return 0
