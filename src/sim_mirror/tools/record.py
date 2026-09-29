# SPDX-License-Identifier: Apache-2.0
"""`sim_record`: recording the device's screen as an MP4, a GIF or both, with its touches drawn in.

An agent records a demo the way a person does from the viewer: start, do the steps, stop. What stop answers -- each
file's path, size and pixel size -- is what the agent needs to attach it somewhere, such as a pull request.
"""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.recordings import RecordingOptions, RecordingRefused, Recordings
from sim_mirror.protocol import Recording
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, text

ACTIONS = ("start", "stop", "status", "list")
#: How many kept recordings `list` names.
LISTED = 10


def _size(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB" if count >= 100_000 else f"{max(1, round(count / 1000))} KB"


def describe(recording: Recording) -> str:
    """A kept recording as an agent reads it: what it is, then each file."""
    lines = [f"{recording['id']} · {recording['device']} · {recording['duration_ms'] / 1000:.1f}s"]
    for kept in recording["files"]:
        pixels = f" {kept['width']}x{kept['height']}" if kept["width"] else ""
        lines.append(f"{kept['format']}{pixels} · {_size(kept['bytes'])} · {kept['path']}")
    lines += [f"note: {note}" for note in recording["notes"]]
    return "\n".join(lines)


def _flag(args: dict[str, Any], name: str) -> bool | None:
    value = args.get(name)
    if value is not None and not isinstance(value, bool):
        raise ToolRefused(f"{name} is true or false")
    return value


def _recordings(ctx: ToolContext) -> Recordings:
    if ctx.recordings is None:
        raise ToolRefused("recording is not available here")
    return ctx.recordings


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action", "status")
    if action not in ACTIONS:
        raise ToolRefused(f"action is one of {', '.join(ACTIONS)}")
    recordings = _recordings(ctx)
    if action == "list":
        kept = recordings.listing(ctx.config)[:LISTED]
        return text("\n\n".join(describe(recording) for recording in kept) if kept else "no recordings kept yet")
    if action == "status":
        running = ctx.manager.instance(ctx.scope)
        if running is None or running.recording is None:
            return text("nothing is being recorded; sim_record start begins")
        state = running.recording.state(ctx.manager.now())
        return text(f"recording {running.name} for {state['since_ms'] / 1000:.0f}s, started by {state['by']}")
    try:
        if action == "stop":
            running = ctx.manager.instance(ctx.scope)
            if running is None:
                raise ToolRefused("nothing is being recorded")
            return text("kept " + describe(await recordings.stop(running)))
        options = RecordingOptions.from_config(
            ctx.config,
            format=args.get("format"),
            touches=_flag(args, "touches"),
            speed=args.get("speed"),
            status_bar=_flag(args, "status_bar"),
        )
        instance = await ready_device(ctx)
        control = ctx.manager.control(instance)
        await recordings.start(instance, ctx.config, control, by=ctx.caller.title, options=options)
    except RecordingRefused as exc:
        raise ToolRefused(str(exc)) from exc
    return text(
        f"recording {instance.name} as {options.format}; sim_record stop keeps it "
        f"(it stops by itself after {options.max_seconds}s)"
    )


TOOL = make_tool("sim_record", (Capability.RECORD,), run)
