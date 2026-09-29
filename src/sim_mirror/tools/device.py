# SPDX-License-Identifier: Apache-2.0
"""`sim_device`: which device, starting it, restarting it, and how it looks and where it is.

What an agent changes about how the device looks -- light or dark, its status bar, text size, contrast, reduce motion
-- or where it believes it is goes through the device's change ledger (`core.device_changes`), so a real device gets
back what it had when it is let go (`core.device_settings`, which a person's route shares). Each change needs its
capability, so a device whose tool cannot make it refuses before anything is asked of it.
"""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.device_settings import CHANGES, SettingRefused
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.identifiers import build_destination
from sim_mirror.tools.context import ToolContext, make_tool, ready_device, require
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import DEVICE_ACTIONS


def device_line(instance: DeviceInstance) -> str:
    screen = instance.screen
    size = f" · {screen.width_pt}x{screen.height_pt}pt @{screen.scale:g}x" if screen else ""
    connected = f" · {instance.connection}" if instance.connection else ""
    return (
        f"{instance.name} · {instance.runtime} · {instance.state}{connected}{size}\n"
        f"udid {instance.udid}\n"
        f"build for: -destination '{build_destination(instance.udid)}'"
    )


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action", "info")
    if action == "info":
        instance = ctx.manager.instance(ctx.scope)
        if instance is None:
            what = "simulator" if ctx.manager.kind(ctx.scope) == "simulator" else "device"
            return text(f"No {what} is running here; sim_device boot starts one.")
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
    if action not in CHANGES:
        raise ToolRefused(f"action is one of {', '.join(DEVICE_ACTIONS)}")
    needs, change = CHANGES[action]
    instance = await ready_device(ctx)
    require(ctx, instance, (needs,), f"sim_device {action}")
    try:
        said, work = change(args, ctx.manager.changes(instance))
    except SettingRefused as exc:
        raise ToolRefused(str(exc)) from exc
    await ctx.actions.announced(instance, ctx.caller, "device", said, work)
    return text(said)


TOOL = make_tool("sim_device", (Capability.LIFECYCLE,), run)
