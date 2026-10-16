# SPDX-License-Identifier: Apache-2.0
"""A person's "hands off" for agents, kept by the daemon for the hosts that share it: pauses with an expiry.

A host that embeds SimMirror passes its own `InputPause` to `Runtime.build`. A host sharing the daemon builds no
runtime, so the daemon keeps the pause for it, set through a scope's person route: ``PUT
/api/v1/scopes/<scope>/pause`` with how many seconds holds agents' gestures off the device that scope uses, ``DELETE``
lifts it, and ``GET`` says how it stands. A pause runs out by itself, after `PAUSE_MAX_S` at most, so a host that
crashed or forgot cannot leave agents held back for good; one whose person keeps the phone in hand sets it again before
it runs out, as an agent renews its lease. `Pauses` is the daemon's `InputPause`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sim_mirror.seams import HeldDevice

#: The longest one pause holds, in seconds: ten minutes, then it is set again or it lifts.
PAUSE_MAX_S = 600.0


class Pauses:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    def hold(self, scope_id: str, seconds: float) -> float:
        """Pause agents on the scope's device for `seconds` from now, `PAUSE_MAX_S` at most, whatever was set before.
        Answers how long it holds."""
        held = min(seconds, PAUSE_MAX_S)
        self._until[scope_id] = self._clock() + held
        return held

    def lift(self, scope_id: str) -> bool:
        """Lift the scope's pause. Answers whether one was holding."""
        return self._until.pop(scope_id, 0.0) > self._clock()

    def left(self, scope_id: str) -> float:
        """How many seconds the scope's pause still holds; 0 when none does."""
        now = self._clock()
        self._until = {scope: until for scope, until in self._until.items() if until > now}
        return max(0.0, self._until.get(scope_id, now) - now)

    def paused(self, device: HeldDevice) -> bool:
        return any(self.left(scope_id) > 0 for scope_id in device.scopes)
