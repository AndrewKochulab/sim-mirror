# SPDX-License-Identifier: Apache-2.0
"""`sim_device`: which device, starting it, restarting it, and its appearance."""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, text


def device_line(instance: DeviceInstance) -> str:
    screen = instance.screen
    size = f" · {screen.width_pt}x{screen.height_pt}pt @{screen.scale:g}x" if screen else ""
    return (
        f"{instance.name} · {instance.runtime} · {instance.state}{size}\n"
        f"udid {instance.udid}\n"
        f"build for: -destination 'platform=iOS Simulator,id={instance.udid}'"
    )


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action", "info")
    if action == "info":
        instance = ctx.manager.instance(ctx.scope)
        if instance is None:
            return text("No simulator is running here; sim_device boot starts one.")
        return text(device_line(instance))
    if action == "boot":
        return text(device_line(await ready_device(ctx)))
    if action == "restart":
        # Measured: after an XCUITest run a device's apps stop answering accessibility, and only a restart of the
        # device brings them back -- not a reinstall, a relaunch or waiting.
        running = ctx.manager.instance(ctx.scope)
        if running is not None and running.busy:
            raise ToolRefused(f"the device is busy: {running.busy}")
        await ctx.manager.stop(ctx.scope, shutdown_device=True, restarting=True)
        return text("restarted · " + device_line(await ready_device(ctx)))
    if action == "appearance":
        mode = args.get("mode")
        if mode not in ("light", "dark"):
            raise ToolRefused("appearance takes a mode of light or dark")
        instance = await ready_device(ctx)
        await ctx.manager.simctl(instance).appearance(instance.udid, mode)
        return text(f"appearance {mode}")
    raise ToolRefused("action is one of info, boot, restart, appearance")


TOOL = make_tool("sim_device", (Capability.LIFECYCLE,), run)
