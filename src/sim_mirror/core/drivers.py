# SPDX-License-Identifier: Apache-2.0
"""One agent drives a device at a time.

Two agents' taps interleaved on one phone spoil both their runs, and neither can tell. So each device remembers its
driver (`instance.Driver`): the agent whose call last changed it -- `sim_act`; `sim_app` launching, quitting or
installing an app or opening a URL; `sim_build_run` and `sim_test`; and `sim_device` booting, restarting, stopping,
turning or setting the device (`CHANGING`). Looking never does: a snapshot, a screenshot, `sim_device info`, `list`
and `health` are answered whoever drives.

While a call of the driver's is under way, or it has acted within `HOLD_S`, another agent's call that would change the
device is refused with who holds it and since when (`HostCopy.device_held`), where ``device.exclusive`` says so -- on a
real device by default, where a person's test run is at stake -- unless the call says ``steal: true``, which takes it
over. After `HOLD_S` with no call from its driver the device passes over by itself, saying nothing; and at once when the
driver's connection ends -- its token revoked as the agent's MCP server closes -- so an agent started again is not kept
off its own device. Where it is not refused -- a simulator by default, or a call that steals -- the answer says another
agent also drove the device, and how long ago (`HostCopy.also_drove`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from sim_mirror.core.instance import DeviceInstance, Driver
from sim_mirror.host_copy import HostCopy
from sim_mirror.seams import Caller

#: How long after its last call a driver holds a device.
HOLD_S = 120.0
#: The tools that change a device, and -- where only some of a tool's actions do -- those actions.
CHANGING: dict[str, frozenset[str] | None] = {
    "sim_act": None,
    "sim_app": frozenset({"launch", "terminate", "install", "open_url"}),
    "sim_build_run": None,
    "sim_test": None,
    "sim_device": frozenset(
        {
            "boot",
            "restart",
            "stop",
            "appearance",
            "status_bar",
            "location",
            "clear_location",
            "text_size",
            "contrast",
            "reduce_motion",
            "rotate",
        }
    ),
}


def changes_device(name: object, arguments: object) -> bool:
    """Whether a call to this tool with these arguments changes the device, rather than only reading it."""
    if not isinstance(name, str) or name not in CHANGING:
        return False
    actions = CHANGING[name]
    return isinstance(arguments, dict) and (actions is None or arguments.get("action") in actions)


class Held(Exception):
    """A call refused because another agent drives the device; its message says who, and how to take it."""


def exclusive(mode: str, *, real: bool) -> bool:
    """Whether ``device.exclusive`` set to `mode` lets one agent at a time drive a device: a real one, or one on this
    Mac -- a simulator, an emulator."""
    return mode == "all" or (mode == "real_devices" and real)


class Drivers:
    """Who drives each device, on the runtime's clock -- and this Mac's time of day, said in a refusal."""

    def __init__(
        self,
        copy: HostCopy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._copy = copy or HostCopy()
        self._clock = clock
        self._wall = wall

    def take(self, caller: Caller, instance: DeviceInstance, *, exclusive: bool, steal: bool) -> str:
        """Let this agent's call drive the device, answering what it should be told -- another agent also drove it
        lately -- or "". Raises `Held` when another agent holds it, `exclusive` is on and the call does not steal.
        Each call taken is ended with `done`."""
        now = self._clock()
        driver = instance.driver
        if driver is not None and driver.key == caller.key:
            driver.last_call = now
            driver.calls += 1
            return ""
        note = ""
        if driver is not None and (driver.calls > 0 or now - driver.last_call < HOLD_S):
            quiet = round(now - driver.last_call)
            if exclusive and not steal:
                since = time.strftime("%H:%M:%S", time.localtime(driver.since_wall))
                raise Held(self._copy.device_held(instance.name, driver.title, since, quiet))
            note = self._copy.also_drove(driver.title, quiet)
        instance.driver = Driver(caller.scope.id, caller.key, caller.title, now, self._wall(), now, calls=1)
        return note

    def done(self, caller: Caller, instance: DeviceInstance) -> None:
        """A call that changed the device has ended: its last call is now, and the device is held `HOLD_S` from here
        -- however long the call ran, it was held all along. A device this agent's call brought up is driven by it; one
        another agent took meanwhile stays theirs."""
        driver = instance.driver
        if driver is None:
            now = self._clock()
            instance.driver = Driver(caller.scope.id, caller.key, caller.title, now, self._wall(), now)
        elif driver.key == caller.key:
            driver.last_call = self._clock()
            driver.calls = max(0, driver.calls - 1)

    @staticmethod
    def let_go(key: str, instances: Iterable[DeviceInstance]) -> list[str]:
        """The devices an agent's connection drove, let go now that connection has ended: their udids."""
        held = [instance for instance in instances if instance.driver is not None and instance.driver.key == key]
        for instance in held:
            instance.driver = None
        return [instance.udid for instance in held]
