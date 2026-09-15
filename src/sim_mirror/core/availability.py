# SPDX-License-Identifier: Apache-2.0
"""Whether a scope can have a simulator right now, and which connector would drive it -- or why not.

Asked on every start, every status, every tool call and every reconcile, so a setting switched off since applies at
once: the Mac is not a Mac, the host offers no simulator here, it is switched off, or no connector can reach one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Connector
from sim_mirror.connectors.registry import ConnectorRegistry, Selection
from sim_mirror.host_copy import HostCopy
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource, Policy

ONLY_ON_A_MAC = "The iOS Simulator runs only on a Mac."


@dataclass(frozen=True)
class Verdict:
    config: SimConfig
    #: Why the scope cannot have a simulator now; None when it can.
    reason: str | None
    #: The connector chosen, and what it reported; None when the question never got that far.
    selection: Selection | None = None

    @property
    def connector(self) -> Connector | None:
        return None if self.selection is None else self.selection.connector


class Availability:
    def __init__(
        self,
        *,
        config: ConfigSource,
        policy: Policy,
        registry: ConnectorRegistry,
        copy: HostCopy,
        platform: str = sys.platform,
    ) -> None:
        self.registry = registry
        self._config = config
        self._policy = policy
        self._copy = copy
        self._platform = platform

    async def check(self, scope: Scope) -> Verdict:
        config = self._config.get(scope)
        if self._platform != "darwin":
            return Verdict(config, ONLY_ON_A_MAC)
        if not self._policy.area_enabled(scope):
            return Verdict(config, self._copy.area_off)
        if not config.enabled:
            return Verdict(config, self._copy.off())
        selection = await self.registry.select(config)
        return Verdict(config, selection.refusal, selection)
