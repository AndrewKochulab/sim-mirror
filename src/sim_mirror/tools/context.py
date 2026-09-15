# SPDX-License-Identifier: Apache-2.0
"""What a tool call runs with, and the device it acts on.

Every tool that needs the device brings it up and waits for it -- up to `READY_WAIT_S` -- so the first call just works.
A device shown through a connector that cannot do what the tool needs refuses the call with the connector's name,
rather than failing half-way through it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.instance import READY, STALLED, DeviceInstance
from sim_mirror.core.manager import DeviceManager, SimulatorUnavailable
from sim_mirror.host_copy import HostCopy
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller
from sim_mirror.tools.results import Result, ToolRefused
from sim_mirror.tools.schemas import SCHEMAS
from sim_mirror.validation import Invalid, whole

READY_WAIT_S = 45.0
READY_POLL_S = 0.5

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
    #: Whether the host lets this scope run commands, read for this call.
    shells_allowed: bool = False
    #: The tool being called; set by the registry.
    tool: Tool | None = None

    @property
    def scope(self) -> Scope:
        return self.caller.scope


def whole_arg(value: object, default: int, bounds: tuple[int, int], name: str) -> int:
    try:
        return whole(value, default, bounds, name, None)
    except Invalid as exc:
        raise ToolRefused(str(exc)) from None


async def ready_device(ctx: ToolContext) -> DeviceInstance:
    """The scope's device, brought up if it is not, waited for, and able to do what the tool needs."""
    try:
        instance = await ctx.manager.ensure(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    waited = 0.0
    while instance.state not in (READY, STALLED):
        if not instance.live:
            raise ToolRefused(f"the simulator {instance.state}: {instance.reason or 'no reason was given'}")
        if waited >= READY_WAIT_S:
            raise ToolRefused(f"the simulator is still {instance.state} after {round(waited)}s; call again shortly")
        await ctx.sleep(READY_POLL_S)
        waited += READY_POLL_S
    tool = ctx.tool
    missing = sorted(capability.value for capability in tool.needs - instance.capabilities) if tool else []
    if tool and missing:
        raise ToolRefused(
            f"{tool.name} needs {', '.join(missing)}, which the {instance.connector} connector showing this device "
            f"cannot do. {ctx.copy.doctor_hint}"
        )
    return instance
