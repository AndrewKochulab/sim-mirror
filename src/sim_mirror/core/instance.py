# SPDX-License-Identifier: Apache-2.0
"""One running device: its state, who watches it, and what a viewer is told about it.

A device is one instance however many scopes use it (``shared`` mode puts a whole group on one), keyed by its UDID.
Its state is what a viewer shows:

* ``booting`` -- being booted, waited for, and given its connector;
* ``ready`` -- streaming and, when its connector can, taking input;
* ``stalled`` -- running, but its screen source is failing and retrying, or its connector stopped and is being
  started again;
* ``failed`` -- could not be started, or its connector stopped and would not start again; starting it again starts
  from scratch;
* ``stopped`` -- gone; the last thing a socket is told before it closes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from sim_mirror.connectors.base import Capability, DeviceSession, Screen
from sim_mirror.core.events import EventBus
from sim_mirror.core.frames import FrameHub
from sim_mirror.core.text_overlay import TextOverlay
from sim_mirror.core.tickets import TicketBook
from sim_mirror.protocol import AppHierarchy, Device, DeviceState
from sim_mirror.scope import Scope

BOOTING, READY, STALLED, FAILED, STOPPED = "booting", "ready", "stalled", "failed", "stopped"
#: The states in which a device holds memory and counts against ``device.max_booted``.
LIVE = frozenset({BOOTING, READY, STALLED})

#: Closes one open screen socket, with a WebSocket close code and a reason.
Closer = Callable[[int, str], Awaitable[None]]


@dataclass(eq=False)
class DeviceInstance:
    udid: str
    name: str
    runtime: str
    #: The scope whose settings the device follows: the one that brought it up, handed on to another scope using it
    #: when that one lets go or is switched off.
    owner: Scope
    developer_dir: str
    #: The ids of the scopes using this device: one, or a whole group's in ``shared`` mode.
    scopes: set[str]
    #: Whether SimMirror made the device (and so may delete it when a person asks).
    created: bool
    #: The connector that drives the device.
    connector: str
    capabilities: frozenset[Capability]
    #: Why a lesser connector than asked for drives this device; None otherwise.
    fallback_reason: str | None = None
    #: The connector the settings chose when the device came up: `connector`, unless it could not reach the device and
    #: ``auto`` went on to the next. A device is moved only when the settings choose another.
    chosen: str = ""
    state: str = BOOTING
    reason: str | None = None
    since: float = 0.0
    last_used: float = 0.0
    #: Whether SimMirror booted it, and so may shut it down again.
    booted_by_us: bool = False
    session: DeviceSession | None = None
    screen: Screen | None = None
    hub: FrameHub | None = None
    busy: str | None = None
    #: The app in front sharing its view hierarchy, as an agent's last snapshot read it.
    app_hierarchy: AppHierarchy | None = None
    #: When a person last touched the screen, so an agent's gesture waits for their hand to lift.
    person_touch_at: float = float("-inf")
    tickets: TicketBook = field(default_factory=TicketBook)
    events: EventBus = field(default_factory=EventBus)
    #: The text viewers outline over the screen, told on `events`.
    text: TextOverlay = field(init=False)
    #: Each open screen socket's closer, and the id of the scope that opened it.
    sockets: dict[Closer, str] = field(default_factory=dict)
    #: The scopes using this device, by id -- `scopes` as `Scope`s, so each can be asked about on its own.
    members: dict[str, Scope] = field(default_factory=dict)
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[None] | None = None
    #: Starting a stopped connector again, while that is being tried.
    recovery: asyncio.Task[None] | None = None
    #: The pid each app was last launched with here. simctl answers a launch of an app still running with the pid it
    #: already has, and brings it to the front without starting it again -- which only this can tell apart.
    launched: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.members.setdefault(self.owner.id, self.owner)
        self.text = TextOverlay(self.events)

    @property
    def group(self) -> str:
        return self.owner.group

    @property
    def viewers(self) -> int:
        return len(self.sockets)

    @property
    def live(self) -> bool:
        return self.state in LIVE

    @property
    def may_shut_down(self) -> bool:
        """Whether ending this device may shut it down: SimMirror booted it, or made it.

        Making it is remembered on disk; booting it only by this process, so without the first a device SimMirror made
        and booted would stay booted -- 2-3 GB -- after every restart.
        """
        return self.booted_by_us or self.created

    def describe(self, now: float) -> Device:
        """What a viewer and the status route say about this device."""
        screen = self.screen
        return {
            "udid": self.udid,
            "name": self.name,
            "runtime": self.runtime,
            "state": cast(DeviceState, self.state),
            "reason": self.reason,
            "since_ms": max(0, round((now - self.since) * 1000)),
            "viewers": self.viewers,
            "busy": self.busy,
            "created": self.created,
            "booted_by_us": self.booted_by_us,
            "screen": None
            if screen is None
            else {
                "points": {"w": screen.width_pt, "h": screen.height_pt},
                "pixels": {"w": screen.width_px, "h": screen.height_px},
                "scale": screen.scale,
            },
            "app_hierarchy": self.app_hierarchy,
        }
