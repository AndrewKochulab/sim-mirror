# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror devices``: this Mac's iOS simulators, the real devices connected to it and its Android emulators and
phones, and which a project uses."""

from __future__ import annotations

import argparse
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.mcp.launcher import ensure_daemon

SCOPE_HELP = "for this scope, instead of this folder's project"


def register(commands: Any) -> None:
    command = commands.add_parser(
        "devices", help="list this Mac's simulators, real devices and Android devices, or choose one"
    )
    command.add_argument("--scope", help=SCOPE_HELP)
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    listing = actions.add_parser("list", help="list the devices a project could use (the default)")
    choosing = actions.add_parser("choose", help="use this device for a project from now on")
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
        ctx.say("no iOS simulators, real devices or Android devices are available on this Mac")
    for device in listed:
        made = "  (made by SimMirror)" if device["created"] else ""
        # A real device's kind, connection and what stands in its way come last, after what a simulator has always had.
        how = _kind(device)
        why = f"  ({device['detail']})" if device.get("detail") else ""
        ctx.say(f"{device['udid']}  {device['runtime']}  {device['name']}  {device['state']}{made}{how}{why}")
    return 0


def _kind(device: dict[str, Any]) -> str:
    """What a device is, when it is not a simulator: a real iPhone and how it is connected, or an Android device."""
    kind, connection = device.get("kind"), device.get("connection")
    if kind == "physical":
        return f"  real device, {connection or 'not connected'}"
    if kind == "android":
        return f"  Android phone, {connection}" if connection else "  Android emulator"
    return ""
