# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror record``: recording a project's device, as the viewer's Record button and an agent's `sim_record` do.

``start`` begins -- as the ``recording.*`` settings say, unless ``--format``, ``--width``, ``--quality`` or ``--preset``
says otherwise -- ``stop`` keeps it and says where each file is, and ``list`` names the recordings kept. The daemon
records; this asks it to.
"""

from __future__ import annotations

import argparse
import contextlib
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.core.recordings import FORMATS, PRESETS, QUALITIES, WIDTHS
from sim_mirror.mcp.launcher import ensure_daemon
from sim_mirror.tools.record import describe

LOW, HIGH = WIDTHS


def _width(raw: str) -> int:
    with contextlib.suppress(ValueError):
        if LOW <= (pixels := int(raw)) <= HIGH:
            return pixels
    raise argparse.ArgumentTypeError(f"a whole number of pixels from {LOW} to {HIGH}")


def register(commands: Any) -> None:
    command = commands.add_parser("record", help="record a project's device as an MP4 or a GIF")
    command.add_argument("--scope", help=SCOPE_HELP)
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    start = actions.add_parser("start", help="start recording the device")
    start.add_argument("--format", choices=FORMATS, help="what to keep, instead of recording.format")
    start.add_argument(
        "--width",
        type=_width,
        metavar="PIXELS",
        help=f"how wide the files are, {LOW} to {HIGH}, instead of the settings",
    )
    start.add_argument("--quality", choices=QUALITIES, help="the MP4's bitrate, instead of recording.mp4_quality")
    start.add_argument(
        "--preset", choices=tuple(PRESETS), help="settings made for one place: pr is an MP4 a pull request plays"
    )
    actions.add_parser("stop", help="stop recording and keep it")
    actions.add_parser("list", help="name the recordings kept (the default)")
    for action in actions.choices.values():
        action.add_argument("--scope", default=argparse.SUPPRESS, help=SCOPE_HELP)
    command.set_defaults(handler=run, action="list", format=None, width=None, quality=None, preset=None)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    base = f"/api/v1/scopes/{scope.id}"
    if args.action == "start":
        asked = {"format": args.format, "width": args.width, "quality": args.quality, "preset": args.preset}
        started = client.admin("POST", f"{base}/recording", asked)["recording"]
        ctx.say(f"recording {scope.label} (by {started['by']}); `sim-mirror record stop` keeps it")
    elif args.action == "stop":
        ctx.say("kept " + describe(client.admin("DELETE", f"{base}/recording")["recording"]))
    else:
        kept = client.get(f"{base}/recordings")["recordings"]
        ctx.say("\n\n".join(describe(recording) for recording in kept) if kept else "no recordings kept yet")
    return 0
