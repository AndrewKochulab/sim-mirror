# SPDX-License-Identifier: Apache-2.0
"""What a tool call runs with, and the device it acts on.

Every tool that needs the device brings it up and waits for it -- up to `READY_WAIT_S` -- so the first call just works.
While its connector says what drives the device is still starting within the time it is given -- WebDriverAgent on a
real iPhone, `real_devices.wda.startup_timeout` -- the call keeps waiting until that time is spent, and a little more
(`STARTING_MARGIN_S`), never longer than `STARTING_WAIT_MAX_S` in all. A device still coming up after that is answered
with what it waits for -- WebDriverAgent being started, and for how long it may take, or what last kept it from
starting -- rather than only to call again.

Every tool is listed whatever the device can do, so an agent can tell a tool that is not there from one that cannot be
used now. A device shown through a connector that cannot do what the tool needs refuses the call with the cause and
what would put it right, rather than failing half-way through it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ReportsStarting, ReportsTrouble
from sim_mirror.connectors.iphone.connector import DRIVEN
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.instance import READY, STALLED, DeviceInstance
from sim_mirror.core.manager import DeviceManager, SimulatorUnavailable
from sim_mirror.core.recordings import Recordings
from sim_mirror.core.signing import SigningTeams
from sim_mirror.host_copy import HostCopy
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller
from sim_mirror.tools.results import Result, ToolRefused
from sim_mirror.tools.schemas import SCHEMAS
from sim_mirror.validation import Invalid, whole

READY_WAIT_S = 45.0
READY_POLL_S = 0.5
#: How much longer than the time WebDriverAgent is given to start a call waits for it: enough for the start that just
#: ran out to be noticed and said, rather than refused a moment before it is.
STARTING_MARGIN_S = 5.0
#: The longest a call waits for a device in all, however long its start is given: the most
#: `real_devices.wda.startup_timeout` may be, and well inside the relay's own bound on a call (900 s).
STARTING_WAIT_MAX_S = 600.0

Handler = Callable[[dict[str, Any], "ToolContext"], Awaitable[Result]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    #: What the device's connector must be able to do for the tool to work.
    needs: frozenset[Capability]
    handler: Handler

    def listing(self) -> dict[str, Any]:
        """The tool as an MCP manifest lists it."""
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def make_tool(name: str, needs: Iterable[Capability], handler: Handler) -> Tool:
    """A tool described by the catalogue (`schemas.SCHEMAS`)."""
    description, input_schema = SCHEMAS[name]
    return Tool(name, description, input_schema, frozenset(needs), handler)


@dataclass(frozen=True)
class ToolContext:
    """Everything a call runs with: who is calling, on which scope, and what the host allows."""

    manager: DeviceManager
    actions: AgentActions
    caller: Caller
    #: The scope's settings, read for this call.
    config: SimConfig
    #: Folders an app may be installed from.
    roots: tuple[Path, ...] = ()
    copy: HostCopy = field(default_factory=HostCopy)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    #: Every scope's builds; None where nothing builds.
    builds: BuildRunner | None = None
    #: The folder a build finds its project in.
    folder: Path | None = None
    #: The settings a build in another folder than `folder` runs with (`Runtime._folder_settings`); None for `config`.
    settings_for: Callable[[Path], SimConfig] | None = None
    #: Whether the host lets this scope run commands, read for this call.
    shells_allowed: bool = False
    #: The tool being called; set by the registry.
    tool: Tool | None = None
    #: Every device's recording; None where nothing records.
    recordings: Recordings | None = None
    #: Which team signs for the scope's real device; None where the settings' team is used as it is.
    signing: SigningTeams | None = None

    @property
    def scope(self) -> Scope:
        return self.caller.scope


def whole_arg(value: object, default: int, bounds: tuple[int, int], name: str) -> int:
    try:
        return whole(value, default, bounds, name, None)
    except Invalid as exc:
        raise ToolRefused(str(exc)) from None


def flag_arg(value: object, name: str) -> bool:
    """A true-or-false argument, false when it is left out."""
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ToolRefused(f"{name} is true or false")
    return value


async def ready_device(ctx: ToolContext) -> DeviceInstance:
    """The scope's device, brought up if it is not, waited for, and able to do what the tool needs."""
    try:
        instance = await ctx.manager.ensure(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    waited, limit = 0.0, READY_WAIT_S
    what = "simulator" if instance.kind == "simulator" else "device"
    while instance.state not in (READY, STALLED):
        if not instance.live:
            raise ToolRefused(f"the {what} {instance.state}: {instance.reason or 'no reason was given'}")
        if waited >= limit:
            limit = _starting_limit(ctx, instance, waited)
        if waited >= limit:
            progress = _progress(ctx, instance)
            said = f": {progress}" if progress else ""
            raise ToolRefused(f"the {what} is still {instance.state} after {round(waited)}s{said}; call again shortly")
        await ctx.sleep(READY_POLL_S)
        waited += READY_POLL_S
    if ctx.tool is not None:
        require(ctx, instance, ctx.tool.needs, ctx.tool.name)
    return instance


def _starting_limit(ctx: ToolContext, instance: DeviceInstance, waited: float) -> float:
    """How long a call that has waited `waited` seconds may wait in all: until the time what drives the device is
    given to start is spent, and `STARTING_MARGIN_S` more, at most `STARTING_WAIT_MAX_S` -- or `waited`, no longer,
    when nothing is starting or its time is spent."""
    connector: object = ctx.manager.availability.registry.get(instance.connector)
    left = connector.starting_left(instance.udid) if isinstance(connector, ReportsStarting) else None
    if left is None or left <= 0:
        return waited
    return min(waited + left + STARTING_MARGIN_S, STARTING_WAIT_MAX_S)


def _progress(ctx: ToolContext, instance: DeviceInstance) -> str | None:
    """What the device's connector is doing that keeps it from being ready -- WebDriverAgent being started -- or what
    last kept it from being driven; None when it does not say."""
    connector: object = ctx.manager.availability.registry.get(instance.connector)
    return connector.progress(instance.udid) if isinstance(connector, ReportsTrouble) else None


def require(ctx: ToolContext, instance: DeviceInstance, needs: Iterable[Capability], what: str) -> None:
    """Refuse `what` -- a tool, or one of its actions -- when the device's connector cannot do all it needs, saying
    why it cannot and what would let it."""
    can = ctx.actions.capabilities(instance.capabilities, ctx.config)
    lacking = frozenset(needs) - can
    if lacking & DRIVEN and instance.kind == "physical":
        raise ToolRefused(
            ctx.copy.wda_off() if not ctx.config.wda_enabled else ctx.copy.not_driven(what, instance.live_note)
        )
    missing = sorted(capability.value for capability in lacking)
    if missing:
        note = instance.live_note
        why = f" ({note})" if note else ""
        raise ToolRefused(
            f"{what} needs {', '.join(missing)}, which the {instance.connector} connector showing this device "
            f"cannot do{why}. {ctx.copy.doctor_hint}"
        )
