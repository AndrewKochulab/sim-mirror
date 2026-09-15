# SPDX-License-Identifier: Apache-2.0
"""One screen socket: a hello each way, then a device's frames and events out, and a person's input in.

A host lets a socket in -- with a one-shot ticket (`DeviceManager.consume_ticket`) -- and hands it here with the device
the ticket opened. The relay says hello first (`protocol.server_hello`): the encodings it offers, the connector and what
it can do, and why a lesser connector was chosen. The viewer answers with the encodings it decodes, within
`HELLO_TIMEOUT_S`; the relay picks the viewer's first choice it offers and says which (`protocol.stream_start`), or
closes with 4400 (not a hello) or 4406 (nothing in common). From then on three things run until one of them ends --
the viewer goes away, or the device does:

* **frames**: once the device is ready, the frame hub's stream in the chosen encoding, each a binary message whose
  first byte says its encoding (`protocol.frame`);
* **events**: the device's state first, then everything its `EventBus` carries, as JSON -- how the viewer learns the
  device is ready or stalled, and that an agent's gesture is about to land;
* **input**: the protocol's whitelist (`screen_input.translate`), acted on by `PersonInput` through the session the
  device has now -- a connector attached again is a new session, and input follows it there.

The socket is registered with the manager, which closes it with 4410 when the device is ended. One send at a time:
frames and events go out from different tasks, and a message is whole to the viewer only if no other send starts
inside it. Events still waiting when the relay ends are sent before the socket closes, so a viewer hears that its
device failed rather than only that its screen went away.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Collection
from typing import Any, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, DeviceSession
from sim_mirror.core.events import Event
from sim_mirror.core.instance import READY, DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.screen_input import PersonInput, translate
from sim_mirror.core.status import capability_names
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.protocol import (
    CLOSE_BAD_MESSAGE,
    HELLO_TIMEOUT_S,
    Encoding,
    ProtocolError,
    frame,
    negotiate,
    parse,
    read_client_hello,
    server_hello,
    status_event,
    stream_start,
)

logger = logging.getLogger(__name__)

NORMAL_CLOSURE = 1000


class ScreenSocket(Protocol):
    """What the relay needs of a WebSocket; Starlette's has it."""

    async def send_bytes(self, data: bytes) -> None:
        """Send one binary message."""
        ...

    async def send_text(self, data: str) -> None:
        """Send one text message."""
        ...

    async def receive(self) -> dict[str, Any]:
        """The next ASGI message: text, bytes or a disconnect."""
        ...

    async def close(self, code: int = NORMAL_CLOSURE, reason: str | None = None) -> None:
        """Close the socket."""
        ...


def offered_encodings(config: SimConfig, capabilities: Collection[Capability]) -> list[Encoding]:
    """What a device's screen is offered as: H.264 first, where its connector streams it and settings allow it."""
    if Capability.STREAM_H264 in capabilities and config.stream_encoding != "jpeg":
        return ["h264", "jpeg"]
    return ["jpeg"]


class ScreenRelay:
    """One viewer watching one device."""

    def __init__(
        self,
        socket: ScreenSocket,
        manager: DeviceManager,
        instance: DeviceInstance,
        *,
        config: SimConfig,
        hello_timeout_s: float = HELLO_TIMEOUT_S,
    ) -> None:
        self._socket = socket
        self._manager = manager
        self._instance = instance
        self._config = config
        self._hello_timeout_s = hello_timeout_s
        self._ready = asyncio.Event()
        self._person: PersonInput | None = None
        #: The session `_person` drives: input follows the device to a session attached again.
        self._person_session: DeviceSession | None = None
        self._sending = asyncio.Lock()

    async def run(self) -> None:
        encoding = await self._handshake()
        if encoding is None:
            return
        instance = self._instance
        events = instance.events.subscribe()
        self._manager.attach(instance, self._close)
        loop = asyncio.get_running_loop()
        tasks = [
            loop.create_task(self._frames(encoding)),
            loop.create_task(self._events(events)),
            loop.create_task(self._input()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            instance.events.unsubscribe(events)
            self._manager.detach(instance, self._close)
            with contextlib.suppress(Exception):
                while not events.empty():
                    await self._send_event(events.get_nowait())
            if self._person is not None:
                await self._person.close()
            with contextlib.suppress(Exception):
                await self._socket.close(code=NORMAL_CLOSURE)

    async def _handshake(self) -> Encoding | None:
        """Hello each way: the encoding both sides chose, or None when the socket was closed instead."""
        instance = self._instance
        offered = offered_encodings(self._config, instance.capabilities)
        hello = server_hello(
            encodings=offered,
            connector=instance.connector,
            capabilities=capability_names(instance.capabilities),
            fallback_reason=instance.fallback_reason,
        )
        await self._socket.send_text(json.dumps(hello))
        try:
            message = await asyncio.wait_for(self._socket.receive(), timeout=self._hello_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            await self._socket.close(code=CLOSE_BAD_MESSAGE, reason=f"no hello within {self._hello_timeout_s:g}s")
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        text = message.get("text")
        try:
            encoding = negotiate(offered, read_client_hello(parse(text) if isinstance(text, str) else None))
        except ProtocolError as exc:
            await self._socket.close(code=exc.code, reason=exc.reason)
            return None
        await self._socket.send_text(json.dumps(stream_start(encoding)))
        return encoding

    async def _close(self, code: int, reason: str) -> None:
        await self._socket.close(code=code, reason=reason)

    async def _send_event(self, event: Event) -> None:
        async with self._sending:
            await self._socket.send_text(json.dumps(event))
        if event.get("type") == "status" and event.get("state") == READY:
            self._ready.set()

    async def _events(self, events: asyncio.Queue[Event]) -> None:
        await self._send_event(status_event(self._instance.describe(self._manager.now())))
        while True:
            await self._send_event(await events.get())

    async def _frames(self, encoding: Encoding) -> None:
        await self._ready.wait()
        hub = self._instance.hub
        if hub is None:
            return
        subscriber = hub.subscribe(encoding)
        try:
            while (latest := await subscriber.next()) is not None:
                async with self._sending:
                    await self._socket.send_bytes(frame(encoding, latest.data))
        finally:
            hub.unsubscribe(subscriber)

    async def _input(self) -> None:
        instance = self._instance
        while True:
            message = await self._socket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            screen = instance.screen
            command = translate(parse(text), screen) if isinstance(text, str) and screen is not None else None
            session = instance.session
            if command is None or session is None:
                continue
            if self._person is None or self._person_session is not session:
                if self._person is not None:
                    await self._person.close()
                self._person = PersonInput(
                    session.input,
                    self._manager.simctl(instance),
                    instance.udid,
                    on_touch=lambda: self._manager.person_touched(instance),
                )
                self._person_session = session
            try:
                await self._person.run(command)
            except (ConnectorError, SimctlError) as exc:
                logger.info("input to the simulator %s was not taken: %s", instance.udid, exc)
