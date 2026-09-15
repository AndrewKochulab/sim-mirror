# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror token``: make, list and revoke the daemon's scoped tokens.

The command reads the token file directly -- it runs as the person who installed SimMirror -- and the daemon reads it
on every request, so a revoked token stops working at once. A new token is printed once, on stdout, and never again.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.daemon.tokens import KINDS


def register(commands: Any) -> None:
    command = commands.add_parser("token", help="make, list or revoke the daemon's scoped tokens")
    actions = command.add_subparsers(dest="action", metavar="ACTION", required=True)
    making = actions.add_parser("create", help="make a token; it is printed only this once")
    making.add_argument("--kind", choices=KINDS, required=True)
    making.add_argument("--scope", action="append", required=True, help="a scope id, or * for all; repeatable")
    making.add_argument("--label", default="", help="what the token is for")
    making.add_argument("--root", action="append", default=[], help="a folder an agent token may build in; repeatable")
    actions.add_parser("list", help="list the scoped tokens")
    revoking = actions.add_parser("revoke", help="stop accepting a token")
    revoking.add_argument("id")
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    store = ctx.tokens()
    if args.action == "create":
        roots = [str(Path(root).expanduser().resolve()) for root in args.root]
        record, token = store.create(args.kind, args.scope, label=args.label, roots=roots)
        ctx.say(token)
        ctx.complain(f"made {record.kind} token {record.id} for {', '.join(record.scopes)}; it is shown only this once")
        return 0
    if args.action == "list":
        records = store.records()
        if not records:
            ctx.say("no scoped tokens")
        for record in records:
            label = f"  {record.label}" if record.label else ""
            ctx.say(f"{record.id}  {record.kind}  {', '.join(record.scopes)}{label}")
        return 0
    if store.revoke(args.id):
        ctx.say(f"revoked {args.id}")
        return 0
    ctx.complain(f"there is no token {args.id}")
    return 1
