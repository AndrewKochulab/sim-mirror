# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror open``: this project's simulator in a browser tab, let in with a one-shot code.

The daemon is started when it is not running. The code sits in the URL's fragment, which the browser never sends to
the server, and is spent within a minute; ``--print`` prints the URL instead of opening it. ``--settings`` opens a
session whose settings panel may change settings, for an hour; one without it may only read them.
"""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp.launcher import ensure_daemon


def register(commands: Any) -> None:
    command = commands.add_parser("open", help="open this project's simulator viewer in the browser")
    command.add_argument("--scope", help="the scope to show, instead of this folder's project")
    command.add_argument("--print", dest="print_url", action="store_true", help="print the URL instead of opening it")
    command.add_argument("--settings", action="store_true", help="let this page change the project's settings")
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    made = client.post("/api/v1/admin/login-codes", {"scope": scope.id, "settings": args.settings})
    url = f"{client.url}{made['url']}"
    if args.print_url or not ctx.open_url(url):
        ctx.say(url)
    else:
        ctx.say(f"opened the viewer for {scope.label} ({client.url}/viewer/{scope.id})")
    return 0
