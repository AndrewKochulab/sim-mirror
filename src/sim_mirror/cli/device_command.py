# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror device``: how a project's running device looks, and where it believes it is -- as an agent's
`sim_device` changes them, and put back the same way when the device is let go (`core.device_settings`).
"""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.mcp.launcher import ensure_daemon
from sim_mirror.platform.simctl import CONTENT_SIZES

ON_OFF = ("on", "off")


def register(commands: Any) -> None:
    command = commands.add_parser("device", help="change how a project's device looks, or where it believes it is")
    command.add_argument("--scope", help=SCOPE_HELP)
    actions = command.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("appearance", help="light or dark").add_argument("mode", choices=("light", "dark"))
    actions.add_parser("status-bar", help="a demo status bar, or the device's own").add_argument(
        "preset", choices=("demo", "clear")
    )
    located = actions.add_parser("location", help="where the device believes it is")
    located.add_argument("latitude", type=float)
    located.add_argument("longitude", type=float)
    actions.add_parser("clear-location", help="the device's own location again")
    actions.add_parser("text-size", help="its text size").add_argument("size", choices=CONTENT_SIZES)
    actions.add_parser("contrast", help="increased contrast").add_argument("switch", choices=ON_OFF)
    actions.add_parser("reduce-motion", help="reduce motion, on a real device").add_argument("switch", choices=ON_OFF)
    for action in actions.choices.values():
        action.add_argument("--scope", default=argparse.SUPPRESS, help=SCOPE_HELP)
    command.set_defaults(handler=run)


def asked(args: argparse.Namespace) -> dict[str, Any]:
    """The change the command asks for, as `sim_device` names it."""
    action = str(args.action).replace("-", "_")
    if action in ("contrast", "reduce_motion"):
        return {"action": action, "on": args.switch == "on"}
    fields = {"appearance": "mode", "status_bar": "preset", "text_size": "size"}
    if action in fields:
        return {"action": action, fields[action]: getattr(args, fields[action])}
    if action == "location":
        return {"action": action, "latitude": args.latitude, "longitude": args.longitude}
    return {"action": action}


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    said = client.admin("POST", f"/api/v1/scopes/{scope.id}/device/settings", asked(args))["said"]
    ctx.say(f"{scope.label}: {said}")
    return 0
