# SPDX-License-Identifier: Apache-2.0
"""Which tools an agent is offered, and running a call to one.

Tools come from providers: the device tools always, the build tools while ``build.tools`` is on -- and a host or a
later version adds a provider rather than editing this. A manifest leaves out a tool the scope's connector cannot
serve (a view-only mirror offers no `sim_act`), and a call to one anyway is refused with why (`context.ready_device`).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Sequence
from typing import Any, Protocol

from sim_mirror.build.provider import BuildToolProvider
from sim_mirror.build.xcodebuild import BuildRefused
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import ActionError
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.tools import act, app, device, screenshot, snapshot
from sim_mirror.tools.context import Tool, ToolContext
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import LOOK_AND_ACT


class ToolProvider(Protocol):
    @property
    def tools(self) -> tuple[Tool, ...]:
        """Every tool this provider has, whether offered now or not."""
        ...

    def offered(self, config: SimConfig) -> bool:
        """Whether a scope with these settings is offered the tools."""
        ...

    def instructions(self, config: SimConfig) -> str:
        """What an agent is told, for these settings."""
        ...


class DeviceToolProvider:
    """Looking at and touching the device, and its apps: always offered."""

    tools: tuple[Tool, ...] = (device.TOOL, snapshot.TOOL, screenshot.TOOL, act.TOOL, app.TOOL)

    def offered(self, config: SimConfig) -> bool:
        return True

    def instructions(self, config: SimConfig) -> str:
        return LOOK_AND_ACT


class ToolRegistry:
    def __init__(self, providers: Sequence[ToolProvider] = (DeviceToolProvider(), BuildToolProvider())) -> None:
        self._providers = tuple(providers)
        self._tools = {tool.name: tool for provider in self._providers for tool in provider.tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def manifest(self, config: SimConfig, capabilities: Collection[Capability]) -> dict[str, Any]:
        """The tools a scope with these settings is offered on a connector with these capabilities, and how to use
        them."""
        can = frozenset(capabilities)
        offered = [provider for provider in self._providers if provider.offered(config)]
        return {
            "tools": [tool.listing() for provider in offered for tool in provider.tools if tool.needs <= can],
            "instructions": "".join(provider.instructions(config) for provider in self._providers),
        }

    async def call(self, name: object, arguments: object, ctx: ToolContext) -> Result:
        """Run one tool call, answering with its result -- or with why not, as an error result."""
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return text(f"there is no tool named {name!r}", error=True)
        if not isinstance(arguments, dict):
            return text("arguments must be an object", error=True)
        try:
            return await tool.handler(arguments, dataclasses.replace(ctx, tool=tool))
        except (ToolRefused, ActionError, SimctlError, BuildRefused) as exc:
            return text(str(exc), error=True)
