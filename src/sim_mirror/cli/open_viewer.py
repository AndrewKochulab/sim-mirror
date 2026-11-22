# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror open``: this project's simulator in a browser tab, let in with a one-shot code.

The daemon is started when it is not running. The code sits in the URL's fragment, which the browser never sends to
the server, and is spent within a minute; ``--print`` prints the URL instead of opening it. ``--settings`` opens a
session whose settings panel may change settings, for an hour; one without it may only read them.

The tab shows the project's own device, and follows it when a person or an agent picks another. ``--device`` shows
this one instead (``/viewer/<scope>?device=<udid>``), whichever device the project uses: one tab per device shows two
devices side by side. A device the project could not use is refused before anything is opened.
"""

from __future__ import annotations

import argparse
from typing import Any
from urllib.parse import quote, urlencode

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp.launcher import ensure_daemon


def register(commands: Any) -> None:
    command = commands.add_parser(name="open", help="Open this project's simulator viewer in the browser")
    command.add_argument("--scope", help="the scope to show, instead of this folder's project")
    command.add_argument("--print", dest="print_url", action="store_true", help="print the URL instead of opening it")
    command.add_argument("--settings", action="store_true", help="let this page change the project's settings")
    command.add_argument(
        "--device", metavar="UDID", help="show this device in the tab, instead of following the project's own"
    )
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    place = ""
    if args.device:
        # Not found -- refused, as one line -- when the project could not use it.
        client.get(f"/api/v1/scopes/{scope.id}?udid={quote(args.device, safe='')}")
        place = "?" + urlencode({"device": args.device})
    made = client.post("/api/v1/admin/login-codes", {"scope": scope.id, "settings": args.settings})
    path, _, fragment = str(made["url"]).partition("#")
    url = f"{client.url}{path}{place}#{fragment}"
    if args.print_url or not ctx.open_url(url):
        ctx.say(url)
    else:
        shown = f" showing {args.device}" if args.device else ""
        ctx.say(f"opened the viewer for {scope.label}{shown} ({client.url}/viewer/{scope.id}{place})")
    return 0
