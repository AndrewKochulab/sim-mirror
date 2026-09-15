# SPDX-License-Identifier: Apache-2.0
"""`sim_screenshot`: a JPEG of the screen or a region of it, or several to see an animation."""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, images


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    instance = await ready_device(ctx)
    shots = await ctx.actions.screenshots(
        instance,
        ctx.caller,
        width=args.get("width", ctx.config.screenshot_width),
        region=args.get("region"),
        frames=args.get("frames", 1),
        interval_ms=args.get("interval_ms"),
        max_elements=ctx.config.snapshot_max_elements,
    )
    caption = f"{len(shots)} frame{'s' if len(shots) != 1 else ''} · {shots[0].width}x{shots[0].height}px"
    return images(caption, [shot.jpeg for shot in shots])


TOOL = make_tool("sim_screenshot", (Capability.SCREENSHOT,), run)
