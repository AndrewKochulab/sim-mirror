# SPDX-License-Identifier: Apache-2.0
"""Which connectors are installed, and which one a scope's settings get.

The built-in connectors are always there; others come from packages registering a factory in the
``sim_mirror.connectors`` entry-point group. A factory takes a `ConnectorContext` -- where the host keeps its state,
its words, and simctl -- and answers a `Connector`.

``connectors.preferred`` chooses:

* ``auto`` tries native, then idb, then simctl, and uses the first that can be used here, saying why a lesser one was
  chosen (`Selection.fallback_reason`) -- so a Mac whose native helper cannot be run still has idb when idb_companion
  is installed, and a Mac with neither still shows the screen and says what is missing to touch it. The ones after it
  that can be used too are kept (`Selection.candidates`): a connector that probes well can still fail to reach a
  device, and the next is tried then. It never tries mcpbridge, which reads the screen but cannot touch it and needs
  Xcode 27;
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
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.seams import StateStore

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "sim_mirror.connectors"
#: The connectors ``auto`` tries, in order.
AUTO_ORDER = ("native", "idb", "simctl")


@dataclass(frozen=True)
class ConnectorContext:
    """What a connector factory is given."""

    state: StateStore
    copy: HostCopy
    simctl_for: Callable[[str], Simctl]
    #: How xcrun is run, for a connector that asks it something simctl does not answer.
    xcrun: XcrunRunner = run_xcrun


Factory = Callable[[ConnectorContext], Connector]


@dataclass(frozen=True)
class Selection:
    """The connector a scope gets, what it reported, and why a lesser one -- or none -- was chosen."""

    connector: Connector | None
    report: ConnectorReport | None
    fallback_reason: str | None = None
    refusal: str | None = None
    #: The connectors after the chosen one that can be used too, in the order ``auto`` tries them; empty unless
    #: ``auto`` chose.
    candidates: tuple[tuple[Connector, ConnectorReport], ...] = ()

    @property
    def choices(self) -> tuple[tuple[Connector, ConnectorReport], ...]:
        """The chosen connector and then each candidate, as a device tries them."""
        if self.connector is None or self.report is None:
            return ()
        return ((self.connector, self.report), *self.candidates)


def builtin_factories() -> dict[str, Factory]:
    from sim_mirror.connectors.idb.connector import create as idb
    from sim_mirror.connectors.mcpbridge.connector import create as mcpbridge
    from sim_mirror.connectors.native.connector import create as native
    from sim_mirror.connectors.simctl.connector import create as simctl

    return {"native": native, "idb": idb, "simctl": simctl, "mcpbridge": mcpbridge}


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

    def connectors(self) -> list[Connector]:
        return list(self._connectors.values())

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
        usable: list[tuple[Connector, ConnectorReport]] = []
        for name in AUTO_ORDER:
            connector = self.get(name)
            if connector is None:
                continue
            report = await connector.probe(config)
            if report.available:
                usable.append((connector, report))
            elif not usable:
                unavailable.append(report)
        if usable:
            (connector, report), *rest = usable
            reasons = [reason for other in unavailable for reason in other.reasons]
            return Selection(connector, report, fallback_reason=" ".join(reasons) or None, candidates=tuple(rest))
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
