# SPDX-License-Identifier: Apache-2.0
"""`sim_act`: a batch of steps played on the device, answered step by step and with what changed on screen."""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, text


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    snapshot = args.get("snapshot", "diff")
    if snapshot not in ("diff", "full", "none"):
        raise ToolRefused("snapshot is diff, full or none")
    instance = await ready_device(ctx)
    answer = await ctx.actions.act(
        instance,
        ctx.caller,
        args.get("steps"),
        wait=args.get("wait"),
        snapshot=snapshot,
        cursor=ctx.config.agent_cursor,
        lead_ms=ctx.config.cursor_lead_ms,
        max_elements=ctx.config.snapshot_max_elements,
    )
    return text(answer, error=any(line.startswith("error step") for line in answer.splitlines()))


TOOL = make_tool("sim_act", (Capability.INPUT_TOUCH,), run)
