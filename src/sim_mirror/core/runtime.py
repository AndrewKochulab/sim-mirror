# SPDX-License-Identifier: Apache-2.0
"""The composition root: one SimMirror, put together from a host's seams -- or a standalone install's.

A host builds one `Runtime` per process (`Runtime.build`), starts it once its event loop runs, tells it when settings
change (`reconcile`, awaited before the change is answered, so a switch that is off is off by then), and closes it on
the way out. Routers, the daemon and the CLI reach devices, agent tools and builds through it, and nothing else puts
those together.

Whether a scope's agents may use their tools is answered here, on every manifest and every call, from the settings as
they are now: a simulator, its agent tools or the host's area switched off since an agent connected answers with why,
not with a device.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.availability import Availability
from sim_mirror.core.devices import DeviceDirectory, JsonDeviceMemory
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.reaper import Reaper
from sim_mirror.core.screen_relay import ScreenRelay, ScreenSocket
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller, ConfigSource, DeviceMemory, Policy, StateStore, UsageProbe
from sim_mirror.storage.claims import Claims
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, text


@dataclass
class Runtime:
    config: ConfigSource
    state: StateStore
    policy: Policy
    copy: HostCopy
    registry: ConnectorRegistry
    manager: DeviceManager
    actions: AgentActions
    tools: ToolRegistry
    builds: BuildRunner
    reaper: Reaper
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    @classmethod
    def build(
        cls,
        *,
        config: ConfigSource,
        state: StateStore,
        policy: Policy,
        copy: HostCopy | None = None,
        usage: UsageProbe | None = None,
        memory: DeviceMemory | None = None,
        registry: ConnectorRegistry | None = None,
        claims: Claims | None = None,
        tools: ToolRegistry | None = None,
        builds: BuildRunner | None = None,
        xcrun: XcrunRunner = run_xcrun,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        platform: str = sys.platform,
    ) -> Runtime:
        """SimMirror over these seams; every other part has a default a host may replace."""
        copy = copy or HostCopy()

        def simctl_for(developer_dir: str) -> Simctl:
            return Simctl(xcrun, developer_dir=developer_dir)

        registry = registry or ConnectorRegistry.discover(
            ConnectorContext(state=state, copy=copy, simctl_for=simctl_for)
        )
        availability = Availability(config=config, policy=policy, registry=registry, copy=copy, platform=platform)
        manager = DeviceManager(
            config=config,
            availability=availability,
            directory=DeviceDirectory(memory or JsonDeviceMemory(state), copy),
            claims=claims or Claims(state.claims_dir(), owner=copy.owner_name),
            simctl_for=simctl_for,
            copy=copy,
            usage=usage,
            clock=clock,
            sleep=sleep,
        )
        return cls(
            config=config,
            state=state,
            policy=policy,
            copy=copy,
            registry=registry,
            manager=manager,
            actions=AgentActions(manager, config, clock=clock, sleep=sleep),
            tools=tools or ToolRegistry(),
            builds=builds or BuildRunner(state, xcrun=xcrun),
            reaper=Reaper(manager, sleep=sleep),
            sleep=sleep,
        )

    # -- lifetime ---------------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """End what an earlier run left behind, then keep ending what is switched off or idle."""
        await self.manager.start_at_boot()
        self.reaper.start()

    async def close(self) -> None:
        """Stop reaping, end every build, and let go of every device -- the devices themselves keep running."""
        await self.reaper.stop()
        await self.builds.shutdown()
        await self.manager.shutdown()

    async def reconcile(self, group: str | None = None) -> None:
        """Act on changed settings for one group of scopes, or all: devices first, then builds that may not run now."""
        await self.manager.reconcile(group)
        for run in self.builds.runs():
            scope = run.scope
            if group is not None and scope.group != group:
                continue
            if await self.manager.unavailable(scope) or not self.config.get(scope).build_tools:
                await self.builds.cancel(scope.id)

    # -- agents -----------------------------------------------------------------------------------------------------

    async def refusal(self, scope: Scope) -> str | None:
        """Why this scope's agents may not use their tools right now, or None."""
        reason = await self.manager.unavailable(scope)
        if reason:
            return reason
        if not self.config.get(scope).agent_tools:
            return self.copy.agent_tools_off()
        return None

    async def manifest(self, scope: Scope) -> dict[str, Any]:
        """The tools this scope's agents are offered and how to use them -- or none, and why."""
        refused = await self.refusal(scope)
        if refused:
            return {"tools": [], "instructions": refused}
        status = await self.manager.status(scope)
        capabilities = {Capability(name) for name in status["capabilities"]}
        return self.tools.manifest(self.config.get(scope), capabilities)

    def tool_context(self, caller: Caller) -> ToolContext:
        """What a call by this agent runs with, read from the settings and the host's policy as they are now."""
        scope = caller.scope
        return ToolContext(
            manager=self.manager,
            actions=self.actions,
            caller=caller,
            config=self.config.get(scope),
            roots=self.policy.install_roots(scope),
            copy=self.copy,
            sleep=self.sleep,
            builds=self.builds,
            folder=self.policy.build_folder(scope),
            shells_allowed=self.policy.shells_allowed(scope),
        )

    async def call(self, caller: Caller, name: object, arguments: object) -> Result:
        """Run one tool call for this agent -- or answer why not, as an error result."""
        refused = await self.refusal(caller.scope)
        if refused:
            return text(refused, error=True)
        return await self.tools.call(name, arguments, self.tool_context(caller))

    # -- viewers ----------------------------------------------------------------------------------------------------

    def relay(self, socket: ScreenSocket, instance: DeviceInstance) -> ScreenRelay:
        """A screen socket's relay to the device its ticket opened, with its owner's settings as they are now."""
        return ScreenRelay(socket, self.manager, instance, config=self.config.get(instance.owner))
