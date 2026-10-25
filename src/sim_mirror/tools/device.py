# SPDX-License-Identifier: Apache-2.0
"""`sim_device`: which device, switching to another, starting it, restarting it, stopping it, and how it looks and where
it is.

An agent lists the devices its scope could use -- this Mac's simulators, then the iPhones and iPads connected to it --
and switches to one, as a person does in the viewer's picker; a real device only while ``real_devices.agents_choose``
lets agents pick one. Whoever watches sees the switch: the viewer follows to the new device.

A real device is never powered on, restarted or shut down by SimMirror, and what it says does not pretend otherwise:
``boot`` opens a session on it -- the screen, and touch where WebDriverAgent is set up -- and ``restart`` starts that
session over, leaving the device as it is. A simulator's ``restart`` still shuts it down and boots it again.

What an agent changes about how the device looks -- light or dark, its status bar, text size, contrast, reduce motion
-- or where it believes it is goes through the device's change ledger (`core.device_changes`), so a real device gets
back what it had when it is let go (`core.device_settings`, which a person's route shares). Each change needs its
capability, so a device whose tool cannot make it refuses before anything is asked of it.

``rotate`` turns a simulator as Simulator.app's Device > Rotate does (`AgentActions.rotate`), through a connector that
can -- the native one -- and never a real device, which is turned in the hand. ``to`` names the way the app's interface
should lie, as snapshots say it, not the way UIKit names the device: a device turned landscape left shows its
interface landscape right. The answer says how the app on screen was then read lying, which is not always as asked: an
app that runs only upright stays portrait.

``stop`` ends this scope's session on its device -- the screens closed, the connector's companion stopped -- and, with
``shutdown``, shuts a simulator down too. Never a real device: SimMirror does not shut down or power off hardware, and
a stop says it was left on. Nor a simulator another scope still uses, which keeps running for it.
"""

from __future__ import annotations

from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.device_settings import CHANGES, SettingRefused
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.perception.orientation import Orientation
from sim_mirror.platform.identifiers import build_destination, kind_of
from sim_mirror.protocol import DeviceChoice
from sim_mirror.tools.context import ToolContext, flag_arg, make_tool, ready_device, require
from sim_mirror.tools.results import SHUT_DOWN, Result, ToolRefused, text
from sim_mirror.tools.schemas import DEVICE_ACTIONS, ROTATIONS

#: What `sim_device restart` says of a real device: what was started over is SimMirror's session, not the device.
NOT_RESTARTED = "the device itself was not restarted"
#: What `sim_device info` says before a real device's session is open: `boot` opens one, and powers nothing on.
NO_SESSION = "No session is open on the real device here; sim_device boot opens one (it does not power the device on)."


def device_line(instance: DeviceInstance) -> str:
    interface = instance.interface
    size = ""
    if interface is not None:
        # The interface's size, as snapshots measure it: an app turned to landscape is wider than it is tall.
        screen = interface.screen
        turned = f" {interface.words()}" if interface.turned else ""
        size = f" · {screen.width_pt}x{screen.height_pt}pt @{screen.scale:g}x{turned}"
    connected = f" · {instance.connection}" if instance.connection else ""
    return (
        f"{instance.name} · {instance.runtime} · {instance.state}{connected}{size}\n"
        f"udid {instance.udid}\n"
        f"build for: -destination '{build_destination(instance.udid)}'"
    )


def choice_line(choice: DeviceChoice, chosen: bool) -> str:
    """One device an agent could switch to, as `sim_device list` shows it."""
    real = choice["kind"] == "physical"
    parts = [
        "real device" if real else "simulator",
        choice["name"],
        choice["runtime"],
        choice["state"],
        *((choice["connection"],) if real and choice["connection"] else ()),
        *((choice["detail"],) if choice["detail"] else ()),
        f"udid {choice['udid']}",
        *(("in use here",) if chosen else ()),
        *(() if choice["usable"] else ("cannot be used now",)),
    ]
    return " · ".join(parts)


async def _listing(ctx: ToolContext) -> str:
    try:
        listed = await ctx.manager.devices(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    running = ctx.manager.instance(ctx.scope)
    lines = [choice_line(choice, running is not None and choice["udid"] == running.udid) for choice in listed]
    if any(choice["kind"] == "physical" for choice in listed) and not ctx.config.real_devices_agents_choose:
        lines.append(ctx.copy.agents_may_not_choose())
    return "\n".join(lines) or "No device is available here."


async def _choose(args: dict[str, Any], ctx: ToolContext) -> Result:
    udid = args.get("udid")
    if not isinstance(udid, str) or not udid:
        raise ToolRefused("choose needs the udid of a device sim_device list shows")
    if kind_of(udid) == "physical" and not ctx.config.real_devices_agents_choose:
        raise ToolRefused(ctx.copy.agents_may_not_choose())
    running = ctx.manager.instance(ctx.scope)
    if running is not None and running.udid == udid:
        return text("already using " + device_line(running))
    if running is not None and running.busy:
        raise ToolRefused(f"the device is busy: {running.busy}")
    try:
        await ctx.manager.choose(ctx.scope, udid, restarting=True)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    instance = await ready_device(ctx)
    await ctx.actions.announced(instance, ctx.caller, "device", f"switched to {instance.name}", _nothing())
    return text("now using " + device_line(instance))


async def _nothing() -> None:
    return None


async def _stop(args: dict[str, Any], ctx: ToolContext) -> Result:
    """End this scope's session on its device; with ``shutdown``, shut a simulator nobody else uses down too."""
    shutdown = flag_arg(args.get("shutdown"), "shutdown")
    running = ctx.manager.instance(ctx.scope)
    if running is None:
        return text("Nothing is running here to stop; sim_device boot starts the device.")
    if running.busy:
        raise ToolRefused(f"the device is busy: {running.busy}")
    real = not ctx.manager.runs_here(running)
    shared = bool(running.scopes - {ctx.scope.id})
    await ctx.manager.stop(ctx.scope, shutdown_device=shutdown and not real and not shared)
    if real:
        kept = "a real device is never shut down by SimMirror, so it stays on"
    elif shared:
        kept = f"another {ctx.copy.scope_noun} still uses it, so it keeps running"
    elif shutdown:
        kept = "the simulator is shut down"
    else:
        kept = "the simulator keeps running, and shutdown: true shuts it down too"
    return text(f"stopped {running.name}: {kept}. The next call that needs the device starts it again.")


async def _restart(ctx: ToolContext) -> Result:
    running = ctx.manager.instance(ctx.scope)
    if running is not None and running.busy:
        raise ToolRefused(f"the device is busy: {running.busy}")
    if ctx.manager.kind(ctx.scope) == "physical":
        return await _reconnect(ctx)
    # Measured: after an XCUITest run a device's apps stop answering accessibility, and only a restart of the
    # device brings them back -- not a reinstall, a relaunch or waiting.
    await ctx.manager.stop(ctx.scope, shutdown_device=True, restarting=True)
    return text("restarted · " + device_line(await ready_device(ctx)))


async def _reconnect(ctx: ToolContext) -> Result:
    """A real device's restart: its session started over -- the screen, WebDriverAgent and the log attached again --
    and the device left as it is, since SimMirror never restarts or shuts down a real device. One that is not there
    any more is refused with why, and nothing is used in its place."""
    try:
        again = await ctx.manager.reconnect(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(f"could not reconnect to the device: {exc}") from exc
    instance = await ready_device(ctx)
    said = "reconnected: its session was started over" if again else "connected: a session was opened"
    return text(f"{said}, and {NOT_RESTARTED} · {device_line(instance)}")


async def _rotate(args: dict[str, Any], ctx: ToolContext) -> Result:
    """Turn a simulator, and say how the app on screen then lies: one that does not turn that way stays as it was."""
    to = args.get("to")
    if to not in ROTATIONS:
        raise ToolRefused(f"rotate takes to: one of {', '.join(ROTATIONS)}")
    if ctx.manager.kind(ctx.scope) == "physical":
        raise ToolRefused("a real device is turned in the hand: sim_device rotate turns only a simulator")
    instance = await ready_device(ctx)
    if Capability.ROTATE not in instance.capabilities:
        rebuild = (
            f"; the native helper here was built before it could, and `{ctx.copy.helper_build_command}` builds it again"
            if instance.connector == "native"
            else ", and only the native connector can"
        )
        raise ToolRefused(f"the {instance.connector} connector showing this simulator cannot turn it{rebuild}")
    wanted: Orientation = "portrait_upside_down" if to == "upside_down" else to
    turned = await ctx.actions.rotate(instance, ctx.caller, wanted)
    if turned.orientation is None:
        return text(f"turned the device to {to}\n{turned.changes}")
    lies = _ROTATION_NAMES.get(turned.orientation, turned.orientation)
    interface = instance.interface
    assert interface is not None, "a device read since turning has a screen"
    size = f"{interface.screen.width_pt}x{interface.screen.height_pt}pt"
    if turned.orientation == wanted:
        said = f"turned to {to}: the app on screen lies {lies} · {size}"
    else:
        said = (
            f"turned the device to {to}, but the app on screen stays {lies} · {size}: it does not turn that way "
            "(an app that runs only upright stays portrait, and an iPhone without a Home button is never upside "
            "down). The device stays turned, so an app that can turn will"
        )
    return text(f"{said}\n{turned.changes}")


#: How `sim_device rotate` names the way an app lies: as its ``to`` takes it.
_ROTATION_NAMES = {"portrait_upside_down": "upside_down"}


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action", "info")
    if action == "list":
        return text(await _listing(ctx))
    if action == "choose":
        return await _choose(args, ctx)
    if action == "stop":
        return await _stop(args, ctx)
    if action == "info":
        if await ctx.manager.gone(ctx.scope):
            return text(SHUT_DOWN)
        instance = ctx.manager.instance(ctx.scope)
        if instance is None:
            if ctx.manager.kind(ctx.scope) == "physical":
                return text(NO_SESSION)
            return text("No simulator is running here; sim_device boot starts one.")
        return text(device_line(instance))
    if action == "boot":
        return text(device_line(await ready_device(ctx)))
    if action == "restart":
        return await _restart(ctx)
    if action == "rotate":
        return await _rotate(args, ctx)
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
