# SPDX-License-Identifier: Apache-2.0
"""What happens to a device, for everyone watching it.

Frames go to viewers through `frames.FrameHub`; everything else a viewer is told goes through here: the device's
state, an agent's gesture about to land (which a viewer draws as its pointer), a build starting or finishing, the
device being busy. Each open screen socket subscribes, and an event is a small dict it sends on as JSON.

Nothing is kept. A viewer that connects later is sent the device's state by the socket itself, and does not see
gestures that already happened. A viewer that stops reading loses its oldest events rather than holding the others up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

QUEUE_MAX = 64

Event = Mapping[str, Any]


class EventBus:
    """One device's events, fanned out to every subscriber."""

    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        self._maxsize = maxsize
        self._queues: set[asyncio.Queue[Event]] = set()

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._queues.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._queues)

    def publish(self, event: Event) -> None:
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
