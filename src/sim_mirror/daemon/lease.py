# SPDX-License-Identifier: Apache-2.0
"""Whether an agent is still using a scope's device: leases, renewed by the MCP launcher while its client works.

The reaper ends a device nobody has used for ``device.idle_minutes`` -- unless someone still holds it. A person holds it
by watching; an agent between tool calls watches nothing, so ``sim-mirror mcp`` renews a lease for its scope every
`RENEW_S` from its first tool call for as long as calls keep coming -- within ``device.agent_idle_minutes`` of each
other -- and one not renewed for `LEASE_S` has gone (its client went quiet, quit, or crashed). Revoking the token a
lease was renewed with lets it go at once. `Leases` is the daemon's `UsageProbe`.

A lease starting, lapsing and being let go is logged once each, never each renewal.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from sim_mirror.seams import HeldDevice

logger = logging.getLogger(__name__)

LEASE_S = 90.0
RENEW_S = 30.0


class Leases:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._held: dict[tuple[str, str], float] = {}

    def renew(self, scope_id: str, holder: str, ttl_s: float = LEASE_S) -> float:
        """Hold a scope's device for another `ttl_s`; answers when the lease runs out."""
        self._prune()
        if (scope_id, holder) not in self._held:
            logger.info("an agent holds %s now (token %s)", scope_id, holder)
        expires = self._clock() + ttl_s
        self._held[(scope_id, holder)] = expires
        return expires

    def release(self, scope_id: str, holder: str) -> None:
        if self._held.pop((scope_id, holder), None) is not None:
            logger.info("the agent holding %s let it go (token %s)", scope_id, holder)

    def release_holder(self, holder: str) -> None:
        """Let go of every lease one holder has: its token was revoked, its client gone."""
        for scope_id, held_by in list(self._held):
            if held_by == holder:
                self.release(scope_id, holder)

    def held(self, scope_id: str) -> bool:
        self._prune()
        return any(key[0] == scope_id for key in self._held)

    def scopes(self) -> set[str]:
        """The scopes an agent holds now."""
        self._prune()
        return {scope_id for scope_id, _holder in self._held}

    def holders(self) -> dict[str, set[str]]:
        """Who holds each scope an agent holds now: the ids of the tokens its leases were renewed with."""
        self._prune()
        found: dict[str, set[str]] = {}
        for scope_id, holder in self._held:
            found.setdefault(scope_id, set()).add(holder)
        return found

    def in_use(self, device: HeldDevice) -> bool:
        return any(self.held(scope_id) for scope_id in device.scopes)

    def _prune(self) -> None:
        now = self._clock()
        for (scope_id, holder), expires in list(self._held.items()):
            if expires <= now:
                del self._held[(scope_id, holder)]
                logger.info("the agent lease on %s lapsed (token %s): its client went quiet, or quit", scope_id, holder)
