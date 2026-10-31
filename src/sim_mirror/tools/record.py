# SPDX-License-Identifier: Apache-2.0
"""`sim_record`: recording the device's screen as an MP4, a GIF or both, with its touches drawn in.

An agent records a demo the way a person does from the viewer: start, do the steps, stop. What stop answers -- each
file's path, size and pixel size -- is what the agent needs to attach it somewhere, such as a pull request.
``also: "pr"`` -- on start, or on stop -- keeps a copy for a pull request beside the full-size file. ``max_seconds`` and
``label`` on start say how long it may run and what its file name says; where it is kept stays the person's setting.
``fps`` keeps the MP4 at 30, 60 or 120 frames a second, and ``region`` -- a ref or a rectangle in points, as
`sim_screenshot` takes -- only that part of the screen. What stop answers says how fast the screen sent its pictures,
and start and stop both warn when the recording will stutter.

A recording that stopped by itself is still answered by the next stop, and one still being rendered when stop has
waited a while is said to be, with where it will be kept: status and list say when it is.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import ActionError
from sim_mirror.core.recordings import RecordingOptions, RecordingRefused, Recordings
from sim_mirror.protocol import Recording, RecordingCapture, RecordingFinishing, RecordingStopped
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, text

ACTIONS = ("start", "stop", "status", "list")
#: How many kept recordings `list` names.
LISTED = 10
#: How to learn when a recording still being rendered is kept: `list` says so, where `status` only stops naming it.
KEPT_WHEN = "sim_record list names it as rendering until it is kept, then with its files"


def _size(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB" if count >= 100_000 else f"{max(1, round(count / 1000))} KB"


def describe(recording: Recording) -> str:
    """A kept recording as an agent reads it: what it is, then each file."""
    lines = [f"{recording['id']} · {recording['device']} · {recording['duration_ms'] / 1000:.1f}s"]
    for kept in recording["files"]:
        pixels = f" {kept['width']}x{kept['height']}" if kept["width"] else ""
        lines.append(f"{kept['format']}{pixels} · {_size(kept['bytes'])} · {kept['path']}")
    # A note kept by an older SimMirror has no capture.
    capture = recording.get("capture")
    if capture:
        lines.append(captured(capture))
    lines += [f"note: {note}" for note in recording["notes"]]
    return "\n".join(lines)


def captured(capture: RecordingCapture) -> str:
    """How fast the screen sent its pictures while it was recorded, as an agent reads it."""
    return (
        f"captured: {capture['pictures']} pictures, up to {capture['busiest_fps']} in a second (kept at "
        f"{capture['fps']} fps); longest wait {capture['longest_gap_ms'] / 1000:.2f}s"
    )


def stopped(answer: RecordingStopped, *, ask: str) -> str:
    """What stopping a recording answers, as an agent or a person reads it: kept, or still being rendered -- and that it
    had stopped by itself, when it had. `ask` says how to find out when one still being rendered is kept. An older
    server says only what was kept."""
    recording = answer["recording"]
    head = ""
    if answer.get("stopped_by_itself"):
        head = f"stopped by itself at {answer.get('recorded_ms', 0) / 1000:.0f}s, its limit; "
    if answer.get("rendering"):
        return f"{head}still rendering {describe(recording)}\n{ask}"
    return f"{head}kept {describe(recording)}"


def rendering(finishing: RecordingFinishing) -> str:
    """A recording still being rendered, as an agent reads it."""
    how = "by itself " if finishing["stopped_by_itself"] else ""
    at = f"{finishing['recorded_ms'] / 1000:.0f}s"
    where = " and ".join(finishing["paths"])
    return f"rendering {finishing['id']} · {finishing['device']} (stopped {how}at {at}); it will be kept at {where}"


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
        listed = [rendering(finishing) for finishing in recordings.finishing(ctx.config)]
        listed += [describe(recording) for recording in recordings.listing(ctx.config)[:LISTED]]
        return text("\n\n".join(listed) if listed else "no recordings kept yet")
    if action == "status":
        return text(_status(ctx, recordings))
    try:
        if action == "stop":
            running = ctx.manager.instance(ctx.scope)
            if running is None:
                raise ToolRefused("nothing is being recorded")
            answer = await recordings.stop(running, also=args.get("also"))
            return text(stopped(answer, ask=KEPT_WHEN))
        options = RecordingOptions.from_config(
            ctx.config,
            format=args.get("format"),
            touches=_flag(args, "touches"),
            speed=args.get("speed"),
            status_bar=_flag(args, "status_bar"),
            width=args.get("width"),
            quality=args.get("quality"),
            preset=args.get("preset"),
            also=args.get("also"),
            max_seconds=args.get("max_seconds"),
            label=args.get("label"),
            fps=args.get("fps"),
        )
        instance = await ready_device(ctx)
        crop = await ctx.actions.region(instance, ctx.caller, args.get("region"), ctx.config.snapshot_max_elements)
        options = replace(options, crop=crop)
        control = ctx.manager.control(instance)
        run = await recordings.start(instance, ctx.config, control, by=ctx.caller.title, options=options)
    except (RecordingRefused, ActionError) as exc:
        raise ToolRefused(str(exc)) from exc
    fast = f" at {options.fps} fps" if options.fps != 30 else ""
    part = " (a region of the screen)" if crop is not None else ""
    said = [
        f"recording {instance.name} as {options.format}{fast}{part}; sim_record stop keeps it "
        f"(it stops by itself after {options.max_seconds}s)",
        *(f"warning: {warning}" for warning in run.warnings),
    ]
    return text("\n".join(said))


def _status(ctx: ToolContext, recordings: Recordings) -> str:
    """The recording under way on the scope's device, those still being rendered, and one that stopped by itself."""
    lines: list[str] = []
    running = ctx.manager.instance(ctx.scope)
    if running is not None and running.recording is not None:
        state = running.recording.state(ctx.manager.now())
        lines.append(f"recording {running.name} for {state['since_ms'] / 1000:.0f}s, started by {state['by']}")
    lines += [rendering(finishing) for finishing in recordings.finishing(ctx.config)]
    ended = recordings.ended(running) if running is not None else None
    finishing = ended.finishing if ended is not None else None
    if ended is not None and finishing is not None and finishing.done():
        said = f"{ended.id} stopped by itself at {ended.recorded_s:.0f}s, its limit"
        failure = None if finishing.cancelled() else finishing.exception()
        lines.append(
            f"{said}, and could not be kept: {failure}"
            if failure
            else f"{said}, and is kept; sim_record stop answers with its files"
        )
    return "\n".join(lines) or "nothing is being recorded; sim_record start begins"


TOOL = make_tool("sim_record", (Capability.RECORD,), run)
