# SPDX-License-Identifier: Apache-2.0
"""`sim_device`: which device, starting it, restarting it, and how it looks and where it is.

What an agent changes about how the device looks -- light or dark, its status bar, text size, contrast, reduce motion
-- or where it believes it is goes through the device's change ledger (`core.device_changes`), so a real device gets
back what it had when it is let go. Each change needs its capability, so a device whose tool cannot make it refuses
before anything is asked of it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.device_changes import Changes
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.identifiers import build_destination
from sim_mirror.platform.simctl import CONTENT_SIZES
from sim_mirror.tools.context import ToolContext, make_tool, ready_device, require
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import DEVICE_ACTIONS, SPEED_BOUNDS, WAYPOINTS_MAX

#: What each change needs of the device, and how it is made: the arguments, checked, then the change.
Change = Callable[[dict[str, Any], Changes], tuple[str, Awaitable[None]]]


def device_line(instance: DeviceInstance) -> str:
    screen = instance.screen
    size = f" · {screen.width_pt}x{screen.height_pt}pt @{screen.scale:g}x" if screen else ""
    connected = f" · {instance.connection}" if instance.connection else ""
    return (
        f"{instance.name} · {instance.runtime} · {instance.state}{connected}{size}\n"
        f"udid {instance.udid}\n"
        f"build for: -destination '{build_destination(instance.udid)}'"
    )


def _switch(args: dict[str, Any], action: str) -> bool:
    on = args.get("on")
    if not isinstance(on, bool):
        raise ToolRefused(f"{action} takes on: true or false")
    return on


def _degrees(value: object, limit: int, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not -limit <= value <= limit:
        raise ToolRefused(f"{name} is a number from -{limit} to {limit}")
    return float(value)


def _place(point: object) -> tuple[float, float]:
    if not isinstance(point, list) or len(point) != 2:
        raise ToolRefused("each waypoint is [latitude, longitude]")
    return _degrees(point[0], 90, "latitude"), _degrees(point[1], 180, "longitude")


def _appearance(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    mode = args.get("mode")
    if mode not in ("light", "dark"):
        raise ToolRefused("appearance takes a mode of light or dark")
    return f"appearance {mode}", changes.appearance(mode)


def _status_bar(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    preset = args.get("preset")
    if preset not in ("demo", "clear"):
        raise ToolRefused("status_bar takes a preset of demo or clear")
    said = "demo status bar" if preset == "demo" else "the device's own status bar"
    return said, changes.status_bar(preset == "demo")


def _location(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    waypoints = args.get("waypoints")
    if waypoints is None:
        latitude = _degrees(args.get("latitude"), 90, "latitude")
        longitude = _degrees(args.get("longitude"), 180, "longitude")
        return f"located at {latitude:g}, {longitude:g}", changes.locate(latitude, longitude)
    if not isinstance(waypoints, list) or not 2 <= len(waypoints) <= WAYPOINTS_MAX:
        raise ToolRefused(f"waypoints are 2 to {WAYPOINTS_MAX} [latitude, longitude] pairs")
    speed = args.get("speed", 20)
    low, high = SPEED_BOUNDS
    if isinstance(speed, bool) or not isinstance(speed, int | float) or not low <= speed <= high:
        raise ToolRefused(f"speed is metres a second, from {low:g} to {high:g}")
    points = [_place(point) for point in waypoints]
    return f"moving along {len(points)} waypoints at {speed:g} m/s", changes.route(points, float(speed))


def _clear_location(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    return "location cleared", changes.clear_location()


def _text_size(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    size = args.get("size")
    if size not in CONTENT_SIZES:
        raise ToolRefused(f"text_size takes a size: {', '.join(CONTENT_SIZES)}")
    return f"text size {size}", changes.text_size(size)


def _contrast(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    on = _switch(args, "contrast")
    return f"increased contrast {'on' if on else 'off'}", changes.contrast(on)


def _reduce_motion(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    on = _switch(args, "reduce_motion")
    return f"reduce motion {'on' if on else 'off'}", changes.reduce_motion(on)


CHANGES: dict[str, tuple[Capability, Change]] = {
    "appearance": (Capability.APPEARANCE, _appearance),
    "status_bar": (Capability.STATUS_BAR, _status_bar),
    "location": (Capability.LOCATION, _location),
    "clear_location": (Capability.LOCATION, _clear_location),
    "text_size": (Capability.ACCESSIBILITY, _text_size),
    "contrast": (Capability.ACCESSIBILITY, _contrast),
    "reduce_motion": (Capability.ACCESSIBILITY, _reduce_motion),
}


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
    said, work = change(args, ctx.manager.changes(instance))
    await ctx.actions.announced(instance, ctx.caller, "device", said, work)
    return text(said)


TOOL = make_tool("sim_device", (Capability.LIFECYCLE,), run)
