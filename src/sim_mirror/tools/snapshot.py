# SPDX-License-Identifier: Apache-2.0
"""`sim_snapshot`: the screen as short lines with refs, or what changed since the agent last looked."""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, text


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    mode = args.get("mode", "diff")
    if mode not in ("diff", "full"):
        raise ToolRefused("mode is diff or full")
    instance = await ready_device(ctx)
    return text(
        await ctx.actions.snapshot(instance, ctx.caller, mode=mode, max_elements=ctx.config.snapshot_max_elements)
    )


TOOL = make_tool("sim_snapshot", (Capability.ELEMENT_TREE,), run)
