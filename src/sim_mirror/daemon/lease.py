# SPDX-License-Identifier: Apache-2.0
"""Whether an agent is still using a scope's device: leases, renewed by the MCP launcher while its client runs.

The reaper ends a device nobody has used for ``device.idle_minutes`` -- unless someone still holds it. A person holds it
by watching; an agent between tool calls watches nothing, so ``sim-mirror mcp`` renews a lease for its scope every
`RENEW_S`, and one not renewed for `LEASE_S` has gone (its client quit, or crashed). `Leases` is the daemon's
`UsageProbe`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sim_mirror.seams import HeldDevice

LEASE_S = 90.0
RENEW_S = 30.0


class Leases:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._held: dict[tuple[str, str], float] = {}

    def renew(self, scope_id: str, holder: str, ttl_s: float = LEASE_S) -> float:
        """Hold a scope's device for another `ttl_s`; answers when the lease runs out."""
        expires = self._clock() + ttl_s
        self._held[(scope_id, holder)] = expires
        return expires

    def release(self, scope_id: str, holder: str) -> None:
        self._held.pop((scope_id, holder), None)

    def held(self, scope_id: str) -> bool:
        now = self._clock()
        self._held = {key: expires for key, expires in self._held.items() if expires > now}
        return any(key[0] == scope_id for key in self._held)

    def in_use(self, device: HeldDevice) -> bool:
        return any(self.held(scope_id) for scope_id in device.scopes)
