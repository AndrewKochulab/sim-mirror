# SPDX-License-Identifier: Apache-2.0
"""Which device each agent works on: its binding, inside its scope.

A scope has one device of its own -- the default, which its viewer shows, a person picks and `DeviceManager` keeps
(``_scopes``). Under ``device.selection = "agent"``, the default, each agent connection -- one `Caller.key`, a token
revoked when its MCP server closes -- is also bound to a device of its own: the one it chose with ``sim_device
choose``, else the one its first call that needed a device was given, which is the default -- or, under
``device.agent_default = "free"``, a simulator nobody uses, when another agent already works on the default. Every
call the agent makes then acts on its own device, and one agent's choose never moves another agent: two sessions of
one project keep a device each. ``device.selection = "scope"`` binds nobody, and every agent of a scope shares its one
device, as before 3.9.

An agent is live while a call of its is under way or it made one within ``device.agent_idle_minutes``; one that has
gone quiet longer is no longer counted as working on its device, and its connection ending forgets it at once
(`forget`). Who works on a device is what the runtime tells another agent about before it acts there, and what
``device.exclusive`` refuses it (`core.runtime`) -- the first agent bound to a device is the one working on it, and one
that came later is told so.

Bindings live here, in the runtime, rather than in a host's seams: a host's `DeviceMemory` keeps each scope's default
between runs, and a binding lasts as long as the agent's connection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sim_mirror.scope import Scope
from sim_mirror.seams import Caller, ConfigSource

#: How long an agent goes without a call before it no longer counts as live, when no settings say: the default of
#: ``device.agent_idle_minutes``.
IDLE_S = 30 * 60.0
#: How seldom one agent is told that another works on its device: once per two of them in this many seconds.
TELL_EVERY_S = 120.0


@dataclass
class Agent:
    """One agent connection, as the runtime knows it."""

    scope: Scope
    key: str
    #: What viewers and other agents call it (`Caller.title`).
    title: str
    #: When its last call began or ended, on the runtime's clock.
    last_call: float
    #: Its calls under way now.
    calls: int = 0
    #: The device it works on, or None until its first call that needs one.
    udid: str | None = None
    #: When it was bound to that device.
    since: float = 0.0
    #: Whether it chose that device itself (``sim_device choose``), rather than being given it.
    chosen: bool = False


class Selections:
    """Every agent connection's binding, and when each last called."""

    def __init__(self, config: ConfigSource | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._agents: dict[str, Agent] = {}
        #: When an agent was last told another works on its device: by its key and the other's.
        self._told: dict[tuple[str, str], float] = {}

    def _idle_s(self, scope: Scope) -> float:
        return self._config.get(scope).agent_idle_minutes * 60.0 if self._config is not None else IDLE_S

    def _agent(self, caller: Caller) -> Agent:
        agent = self._agents.get(caller.key)
        if agent is None:
            agent = self._agents[caller.key] = Agent(caller.scope, caller.key, caller.title, self._clock())
        agent.title = caller.title
        return agent

    # -- calls -------------------------------------------------------------------------------------------------------

    def begin(self, caller: Caller) -> None:
        """A call of this agent's starts: it is live until well after it ends."""
        agent = self._agent(caller)
        agent.calls += 1
        agent.last_call = self._clock()

    def end(self, caller: Caller) -> None:
        """A call of this agent's ended."""
        agent = self._agents.get(caller.key)
        if agent is not None:
            agent.calls = max(0, agent.calls - 1)
            agent.last_call = self._clock()

    def live(self, agent: Agent) -> bool:
        """Whether an agent works now: a call under way, or one within its scope's ``device.agent_idle_minutes``."""
        return agent.calls > 0 or self._clock() - agent.last_call < self._idle_s(agent.scope)

    def quiet_s(self, agent: Agent) -> int:
        """How long ago its last call began or ended, in whole seconds."""
        return round(self._clock() - agent.last_call)

    def is_live(self, key: str) -> bool:
        """Whether the agent of this connection is live."""
        agent = self._agents.get(key)
        return agent is not None and self.live(agent)

    # -- bindings ----------------------------------------------------------------------------------------------------

    def of(self, caller: Caller) -> str | None:
        """The device this agent works on, or None while it has none."""
        agent = self._agents.get(caller.key)
        return agent.udid if agent is not None and agent.scope.id == caller.scope.id else None

    def bind(self, caller: Caller, udid: str, *, chosen: bool = False) -> None:
        """This agent works on this device from now on. Bound again to the one it has, it keeps its place there."""
        agent = self._agent(caller)
        if agent.udid != udid or agent.scope.id != caller.scope.id:
            agent.scope, agent.udid, agent.since, agent.chosen = caller.scope, udid, self._clock(), chosen
        elif chosen:
            agent.chosen = True

    def follow(self, scope_id: str, old: str, new: str) -> None:
        """A person moved a scope's own device: its agents working on the old one work on the new one now, in the order
        they came."""
        for agent in self._agents.values():
            if agent.scope.id == scope_id and agent.udid == old:
                agent.udid, agent.chosen = new, False

    def unbind(self, caller: Caller) -> None:
        """This agent works on no device until its next call that needs one."""
        agent = self._agents.get(caller.key)
        if agent is not None:
            agent.udid, agent.chosen = None, False

    def others(self, caller: Caller) -> list[Agent]:
        """The other live agents of this agent's scope that work on a device."""
        return [
            agent
            for agent in self._agents.values()
            if agent.key != caller.key and agent.scope.id == caller.scope.id and agent.udid is not None
            if self.live(agent)
        ]

    def on(self, udid: str, *, besides: str = "") -> list[Agent]:
        """The live agents working on a device, whatever their scope -- but the one keyed `besides` -- the first bound
        first."""
        found = [
            agent for agent in self._agents.values() if agent.udid == udid and agent.key != besides and self.live(agent)
        ]
        return sorted(found, key=lambda agent: agent.since)

    def occupant(self, caller: Caller, udid: str) -> Agent | None:
        """The agent that works on this device, when it is another than this one: the first bound live agent there,
        unless this one was bound to it before them."""
        agent = self._agents.get(caller.key)
        mine = agent.since if agent is not None and agent.udid == udid else None
        for other in self.on(udid, besides=caller.key):
            if mine is None or other.since < mine:
                return other
        return None

    def bound_to(self, scope_id: str, udid: str) -> bool:
        """Whether a live agent of this scope works on this device: it is held for them, not left to idle out."""
        return any(agent.scope.id == scope_id for agent in self.on(udid))

    def tell(self, caller: Caller, other: Agent) -> bool:
        """Whether to tell this agent that the other works on its device: once per two of them in `TELL_EVERY_S`."""
        now = self._clock()
        pair = (caller.key, other.key)
        if now - self._told.get(pair, -TELL_EVERY_S) < TELL_EVERY_S:
            return False
        self._told[pair] = now
        return True

    def forget(self, key: str) -> Agent | None:
        """Forget an agent whose connection ended -- its token revoked -- and what it was told: its device is no longer
        held for it. Answers what was known of it."""
        for pair in [pair for pair in self._told if key in pair]:
            del self._told[pair]
        return self._agents.pop(key, None)
