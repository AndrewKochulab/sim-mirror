# SPDX-License-Identifier: Apache-2.0
"""Which connectors are installed, and which one a scope's settings get.

The built-in connectors are always there; others come from packages registering a factory in the
``sim_mirror.connectors`` entry-point group. A factory takes a `ConnectorContext` -- where the host keeps its state,
its words, and simctl -- and answers a `Connector`.

``connectors.preferred`` chooses:

* ``auto`` tries idb, then simctl, and uses the first that can be used here, saying why a lesser one was chosen
  (`Selection.fallback_reason`) -- so a Mac without idb_companion still shows the screen, and says what to install to
  touch it;
* a connector's name uses that one, or refuses with its reasons -- never another in its place.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Connector, ConnectorReport
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.seams import StateStore

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "sim_mirror.connectors"
#: The connectors ``auto`` tries, in order.
AUTO_ORDER = ("idb", "simctl")


@dataclass(frozen=True)
class ConnectorContext:
    """What a connector factory is given."""

    state: StateStore
    copy: HostCopy
    simctl_for: Callable[[str], Simctl]


Factory = Callable[[ConnectorContext], Connector]


@dataclass(frozen=True)
class Selection:
    """The connector a scope gets, what it reported, and why a lesser one -- or none -- was chosen."""

    connector: Connector | None
    report: ConnectorReport | None
    fallback_reason: str | None = None
    refusal: str | None = None


def builtin_factories() -> dict[str, Factory]:
    from sim_mirror.connectors.idb.connector import create as idb
    from sim_mirror.connectors.simctl.connector import create as simctl

    return {"idb": idb, "simctl": simctl}


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[Connector], *, copy: HostCopy | None = None) -> None:
        self._connectors = {connector.name: connector for connector in connectors}
        self._copy = copy or HostCopy()

    @classmethod
    def discover(
        cls, context: ConnectorContext, *, entry_points: Callable[..., Iterable[Any]] = importlib.metadata.entry_points
    ) -> ConnectorRegistry:
        """The built-in connectors, and every installed one that loads. One that does not is logged and left out."""
        factories = builtin_factories()
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            if entry.name in factories:
                logger.warning("the connector %s is built in; the installed one of that name is not used", entry.name)
                continue
            try:
                factories[entry.name] = entry.load()
            except Exception:
                logger.exception("the installed connector %s could not be loaded", entry.name)
        return cls((factory(context) for factory in factories.values()), copy=context.copy)

    def names(self) -> list[str]:
        return list(self._connectors)

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    async def reports(self, config: SimConfig) -> list[ConnectorReport]:
        """What every connector finds here, for `sim-mirror doctor`."""
        return [await connector.probe(config) for connector in self._connectors.values()]

    async def select(self, config: SimConfig) -> Selection:
        """The connector these settings get."""
        if config.connector != "auto":
            return await self._named(config.connector, config)
        unavailable: list[ConnectorReport] = []
        for name in AUTO_ORDER:
            connector = self.get(name)
            if connector is None:
                continue
            report = await connector.probe(config)
            if report.available:
                reasons = [reason for other in unavailable for reason in other.reasons]
                return Selection(connector, report, fallback_reason=" ".join(reasons) or None)
            unavailable.append(report)
        said = " ".join(reason for report in unavailable for reason in report.reasons) or "No connector is installed."
        return Selection(
            None,
            unavailable[-1] if unavailable else None,
            refusal=f"No connector can reach a simulator here. {said} {self._copy.doctor_hint}",
        )

    async def _named(self, name: str, config: SimConfig) -> Selection:
        connector = self.get(name)
        if connector is None:
            installed = ", ".join(self.names()) or "none"
            return Selection(None, None, refusal=f"There is no connector named {name}; installed: {installed}.")
        report = await connector.probe(config)
        if report.available:
            return Selection(connector, report)
        reasons = " ".join(report.reasons)
        return Selection(
            None, report, refusal=f"The {name} connector cannot be used here. {reasons} {self._copy.doctor_hint}"
        )
