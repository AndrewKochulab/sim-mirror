# SPDX-License-Identifier: Apache-2.0
"""`sim_act`: a batch of steps played on the device, answered step by step and with what changed on screen -- and, when
a step failed because the device was shut down outside SimMirror, saying so.

In a scope in observe mode (``agent.observe``), a step that would tap, long-press or type into an element that reads
like it changes something -- or swipe a list row sideways, drop onto such an element, or press return on a screen with
one -- is refused (`core.refusals.ObserveGuard`) unless this call says ``confirm: true``, which counts for this call
alone.

A call whose step failed, or whose wait was not met -- it gave up, could not read the screen, or was asked for wrongly
-- is an error, though the steps before stay played and the answer says which. ``allow_stale: true`` touches refs as
the agent's last snapshot has them, without reading the screen again when it may have changed out of sight
(`core.actions`). ``steal`` is read by the runtime, before the call gets here (`core.drivers`).
"""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.refusals import ObserveGuard
from sim_mirror.tools.context import ToolContext, flag_arg, make_tool, ready_device
from sim_mirror.tools.results import SHUT_DOWN, Result, ToolRefused, text


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    snapshot = args.get("snapshot", "diff")
    if snapshot not in ("diff", "full", "none"):
        raise ToolRefused("snapshot is diff, full or none")
    confirmed = flag_arg(args.get("confirm"), "confirm")
    guard = ObserveGuard.of(
        ctx.config.agent_observe,
        ctx.config.agent_observe_words,
        confirmed=confirmed,
        languages=ctx.config.agent_observe_languages,
    )
    allow_stale = flag_arg(args.get("allow_stale"), "allow_stale")
    instance = await ready_device(ctx)
    acted = await ctx.actions.perform(
        instance,
        ctx.caller,
        args.get("steps"),
        wait=args.get("wait"),
        snapshot=snapshot,
        cursor=ctx.config.agent_cursor,
        lead_ms=ctx.config.cursor_lead_ms,
        max_elements=ctx.config.snapshot_max_elements,
        guard=guard,
        allow_stale=allow_stale,
    )
    answer = acted.text
    if acted.failed and await ctx.manager.gone(ctx.scope):
        answer += "\n" + SHUT_DOWN
    return text(answer, error=acted.failed or acted.unmet)


TOOL = make_tool("sim_act", (Capability.INPUT_TOUCH,), run)
