# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror record``: recording a project's device, as the viewer's Record button and an agent's `sim_record` do.

``start`` begins -- as the ``recording.*`` settings say, unless ``--format``, ``--width``, ``--quality``, ``--preset``
or ``--max-seconds`` says otherwise, named with ``--label``, and with a copy kept beside it as ``--also`` names --
``stop`` keeps it and says where each file is, or answers the one that stopped by itself, and ``list`` names the
recordings kept and those still being rendered. The daemon records; this asks it to. ``stop`` waits as long as the
daemon waits for rendering, longer than any other request.
"""

from __future__ import annotations

import argparse
import contextlib
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.core.recording_sources import STOP_TIMEOUT_S as SOURCE_STOP_TIMEOUT_S
from sim_mirror.core.recordings import (
    FORMATS,
    FPS,
    LABEL_LENGTH,
    MAX_SECONDS,
    PRESETS,
    QUALITIES,
    STOP_WAIT_S,
    WIDTHS,
)
from sim_mirror.mcp.launcher import ensure_daemon
from sim_mirror.tools.record import describe, rendering, stopped

LOW, HIGH = WIDTHS
SHORTEST, LONGEST = MAX_SECONDS
#: How long ``stop`` waits for the daemon: as long as it waits for the recording to stop and be rendered, and then some.
STOP_TIMEOUT_S = SOURCE_STOP_TIMEOUT_S + STOP_WAIT_S + 30.0


def _width(raw: str) -> int:
    with contextlib.suppress(ValueError):
        if LOW <= (pixels := int(raw)) <= HIGH:
            return pixels
    raise argparse.ArgumentTypeError(f"a whole number of pixels from {LOW} to {HIGH}")


def _seconds(raw: str) -> int:
    with contextlib.suppress(ValueError):
        if SHORTEST <= (seconds := int(raw)) <= LONGEST:
            return seconds
    raise argparse.ArgumentTypeError(f"a whole number of seconds from {SHORTEST} to {LONGEST}")


def _label(raw: str) -> str:
    if 0 < len(raw) <= LABEL_LENGTH and any(character.isascii() and character.isalnum() for character in raw):
        return raw
    raise argparse.ArgumentTypeError(f"up to {LABEL_LENGTH} characters, with a letter or a digit")


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
    start.add_argument("--fps", type=int, choices=FPS, help="frames a second in the MP4, instead of recording.fps")
    start.add_argument(
        "--preset", choices=tuple(PRESETS), help="settings made for one place: pr is an MP4 a pull request plays"
    )
    start.add_argument("--also", choices=tuple(PRESETS), help="also keep a copy as this preset, beside the recording")
    start.add_argument(
        "--max-seconds",
        type=_seconds,
        metavar="SECONDS",
        help=f"stop by itself after this long, {SHORTEST} to {LONGEST}, instead of recording.max_seconds",
    )
    start.add_argument(
        "--label", type=_label, metavar="TEXT", help="joined into the file name: letters and digits, the rest dashes"
    )
    actions.add_parser("stop", help="stop recording and keep it")
    actions.add_parser("list", help="name the recordings kept (the default)")
    for action in actions.choices.values():
        action.add_argument("--scope", default=argparse.SUPPRESS, help=SCOPE_HELP)
    command.set_defaults(
        handler=run,
        action="list",
        format=None,
        width=None,
        quality=None,
        preset=None,
        also=None,
        max_seconds=None,
        label=None,
        fps=None,
    )


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    ensure_daemon(client, ctx.start_daemon)
    base = f"/api/v1/scopes/{scope.id}"
    if args.action == "start":
        asked = {
            "format": args.format,
            "width": args.width,
            "quality": args.quality,
            "preset": args.preset,
            "also": args.also,
            **({"max_seconds": args.max_seconds} if args.max_seconds is not None else {}),
            **({"label": args.label} if args.label is not None else {}),
            **({"fps": args.fps} if args.fps is not None else {}),
        }
        started = client.admin("POST", f"{base}/recording", asked)["recording"]
        ctx.say(f"recording {scope.label} (by {started['by']}); `sim-mirror record stop` keeps it")
    elif args.action == "stop":
        answer = client.admin("DELETE", f"{base}/recording", timeout=STOP_TIMEOUT_S)
        ctx.say(stopped(answer, ask="`sim-mirror record list` says when it is kept"))
    else:
        listed = client.get(f"{base}/recordings")
        said = [rendering(finishing) for finishing in listed.get("finishing") or ()]
        said += [describe(recording) for recording in listed["recordings"]]
        ctx.say("\n\n".join(said) if said else "no recordings kept yet")
    return 0
