# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror settings confirm``: the sensitive settings changes a page is waiting on, and the code for each.

A settings panel that asks to change what SimMirror runs or who may reach it waits for a person to confirm the change
here, with the admin token no page holds. Each change is shown as it would be written; a person who recognises it types
its code into the page.
"""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp.launcher import ensure_daemon

NOTHING_WAITING = "No settings change is waiting to be confirmed."


def register(commands: Any) -> None:
    command = commands.add_parser("settings", help="confirm a settings change a page asked for")
    actions = command.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("confirm", help="show each sensitive change waiting, and the code that confirms it")
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    pending = client.post("/api/v1/admin/settings-confirmations", {})["pending"]
    if not pending:
        ctx.say(NOTHING_WAITING)
        return 0
    for change in pending:
        minutes = max(1, round(float(change["expires_in_s"]) / 60))
        ctx.say(f"{change['summary']}")
        ctx.say(f"  code {change['code']}  (enter it in the page within {minutes} min, only if you asked for this)")
    return 0
