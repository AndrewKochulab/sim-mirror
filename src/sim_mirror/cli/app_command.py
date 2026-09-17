# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror app hierarchy``: what the app in front of a simulator shares through SimMirror's debug SDK.

It reads the booted simulator -- or the one ``--device`` names -- as a snapshot would, under the scope's
``connectors.app`` settings, and prints the app's view hierarchy in a snapshot's lines: a quick way for a developer to
see what an agent will read of a screen, and why an app is not read at all. ``--json`` prints the same as data; neither
ever shows an app's secret. Exits 0 when an app shared its hierarchy, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.document import AppDocument
from sim_mirror.connectors.app.merge import as_hierarchy
from sim_mirror.connectors.app.reader import AppProbe, probe
from sim_mirror.connectors.base import Screen
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.perception.snapshot import build
from sim_mirror.platform.device_data import device_data_dir
from sim_mirror.platform.simctl import Simctl, SimctlError, is_udid

#: Where lines are placed when an app did not say how large its screen is: anywhere on it counts as on screen.
UNKNOWN_SCREEN = Screen(0, 0, 100_000, 100_000, 1.0)


def register(commands: Any) -> None:
    command = commands.add_parser("app", help="see what an app shares through SimMirror's debug SDK")
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    hierarchy = actions.add_parser("hierarchy", help="print the view hierarchy the app in front shares")
    hierarchy.add_argument("--device", help="the simulator (UDID) to read, instead of the one booted")
    hierarchy.add_argument("--scope", help="use this scope's connectors.app settings, instead of this folder's")
    hierarchy.add_argument("--json", action="store_true", help="print it as JSON")
    command.set_defaults(handler=run, action="hierarchy", device=None, scope=None, json=False)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    return asyncio.run(_hierarchy(args, ctx))


async def _device(named: str | None, ctx: CliContext, developer_dir: str) -> str | None:
    """The simulator to read: the one named, else the only one booted. Says why not when there is none."""
    if named is not None:
        if is_udid(named):
            return named
        ctx.complain(f"sim-mirror: {named!r} is not a simulator's UDID")
        return None
    try:
        booted = [
            device.udid for device in await Simctl(ctx.xcrun, developer_dir=developer_dir).devices() if device.booted
        ]
    except SimctlError as exc:
        ctx.complain(f"sim-mirror: {exc}")
        return None
    if len(booted) == 1:
        return booted[0]
    if booted:
        ctx.complain(f"sim-mirror: {len(booted)} simulators are booted: name one with --device ({', '.join(booted)})")
    else:
        ctx.complain("sim-mirror: no simulator is booted: boot one, or name it with --device")
    return None


def _lines(document: AppDocument, max_nodes: int) -> list[str]:
    tree = tree_from_document(document.document, source=wire.SOURCE)
    snapshot = build(tree, device="", screen=document.screen or UNKNOWN_SCREEN, max_elements=max_nodes)
    return [element.line() for element in snapshot.elements]


def _as_data(udid: str, found: AppProbe, notes: tuple[str, ...]) -> dict[str, Any]:
    document = found.asked.document
    return {
        "device": udid,
        "app": as_hierarchy(document.app) if document is not None else None,
        "protocol": document.protocol if document is not None else None,
        "views": document.elements if document is not None else 0,
        "truncated": document.truncated if document is not None else False,
        "took_s": round(found.took_s, 3),
        "notes": [*notes, *(document.notes if document is not None else ())],
        "elements": document.document["elements"] if document is not None else [],
        "listed": [listing.bundle_id for listing in found.listings],
        "unread": [
            {"app": listing.name, "bundle_id": listing.bundle_id, "reason": str(error)}
            for listing, error in found.asked.unread()
        ],
    }


async def _hierarchy(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    udid = await _device(args.device, ctx, config.developer_dir)
    if udid is None:
        return 1
    copy = HostCopy()
    found = await probe(
        udid,
        device_data_dir(udid, env=ctx.env, home=ctx.home),
        max_nodes=config.app_max_nodes,
        timeout_s=config.app_timeout_ms / 1000,
    )
    notes = found.asked.notes(copy, config.app_max_nodes)
    document = found.asked.document
    if args.json:
        ctx.say(json.dumps(_as_data(udid, found, notes), indent=2))
        return 0 if document is not None else 1
    if document is None:
        if found.asked.unread():
            for note in notes:
                ctx.complain(f"sim-mirror: {note}")
        elif found.listings:
            shares = "1 app shares" if len(found.listings) == 1 else f"{len(found.listings)} apps share"
            ctx.complain(f"sim-mirror: {shares} a view hierarchy on {udid}, and none is in front")
        else:
            ctx.complain(f"sim-mirror: {udid}: {copy.app_hierarchy_none()}")
        return 1
    app = document.app
    ctx.say(
        f"{app.name} · {app.bundle_id} · SDK {app.sdk_version} · {document.elements} views of {udid} "
        f"· read in {found.took_s:.2f}s"
    )
    for line in [*_lines(document, config.app_max_nodes), *notes, *document.notes]:
        ctx.say(line)
    return 0
