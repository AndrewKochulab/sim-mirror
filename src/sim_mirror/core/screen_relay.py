# SPDX-License-Identifier: Apache-2.0
"""One screen socket: a hello each way, then a device's frames and events out, and a person's input in.

A host lets a socket in -- with a one-shot ticket (`DeviceManager.consume_ticket`) -- and hands it here with the device
the ticket opened. The relay says hello first (`protocol.server_hello`): the encodings it offers, the connector and what
it can do, and why a lesser connector was chosen. The viewer answers with the encodings it decodes, within
`HELLO_TIMEOUT_S`; the relay picks the viewer's first choice it offers and says which (`protocol.stream_start`), or
closes with 4400 (not a hello) or 4406 (nothing in common). From then on three things run until one of them ends --
the viewer goes away, or the device does:

* **frames**: once the device is ready -- or stalled, since frames coming again are what makes a stalled device ready
  -- the frame hub's stream in the chosen encoding, each a binary message whose first byte says its encoding
  (`protocol.frame`);
* **events**: the device's state first, then everything its `EventBus` carries, as JSON -- how the viewer learns the
  device is ready or stalled, and that an agent's gesture is about to land;
* **input**: the protocol's whitelist (`screen_input.translate`), acted on by `PersonInput` through the session the
  device has now -- a connector attached again is a new session, and input follows it there.

The socket is registered with the manager from before its hello, so a device ended at any point closes it (4410, or
4412 for a restart). One send at a time:
frames and events go out from different tasks, and a message is whole to the viewer only if no other send starts
inside it. Events still waiting when the relay ends are sent before the socket closes, so a viewer hears that its
device failed rather than only that its screen went away.

Every send and close has a deadline (`SEND_TIMEOUT_S`). A page the browser froze keeps its socket open but reads
nothing, so a send to it would wait for ever and keep its device in use; a viewer that takes nothing for that long
is let go as if it had left (`ViewerGone`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Collection
from typing import Any, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, DeviceSession
from sim_mirror.core.events import Event
from sim_mirror.core.instance import READY, STALLED, DeviceInstance
from sim_mirror.core.manager import STOPPED_REASON, DeviceManager
from sim_mirror.core.screen_input import PersonInput, translate
from sim_mirror.core.status import capability_names
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.protocol import (
    CLOSE_BAD_MESSAGE,
    CLOSE_STOPPED,
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
from sim_mirror.scope import Scope

logger = logging.getLogger(__name__)

NORMAL_CLOSURE = 1000
#: How long a send or a close may wait on a viewer. A page the browser froze keeps its socket open but takes nothing,
#: so a send to it never finishes; a viewer that takes nothing for this long is gone.
SEND_TIMEOUT_S = 10.0


class ViewerGone(Exception):
    """A viewer took nothing it was sent for the send deadline: its relay ends as if it had left."""


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
        send_timeout_s: float = SEND_TIMEOUT_S,
        scope: Scope | None = None,
    ) -> None:
        self._socket = socket
        self._manager = manager
        self._instance = instance
        self._config = config
        self._hello_timeout_s = hello_timeout_s
        self._send_timeout_s = send_timeout_s
        #: The scope whose ticket opened this socket: switched off, it closes this socket alone on a shared device.
        self._scope_id = scope.id if scope is not None else None
        self._ready = asyncio.Event()
        self._person: PersonInput | None = None
        #: The session `_person` drives: input follows the device to a session attached again.
        self._person_session: DeviceSession | None = None
        self._sending = asyncio.Lock()

    async def run(self) -> None:
        instance = self._instance
        if not self._manager.is_current(instance):
            # Ended between its ticket and now: there is nothing to show, and nothing comes back on this socket.
            await self._close_within(CLOSE_STOPPED, STOPPED_REASON)
            return
        # The manager's from before the hello, so a device ended during the hello closes this socket too.
        self._manager.attach(instance, self._close, self._scope_id)
        try:
            encoding = await self._handshake()
        except ViewerGone:
            await self._close_within(NORMAL_CLOSURE)
            encoding = None
        if encoding is None:
            self._manager.detach(instance, self._close)
            return
        events = instance.events.subscribe()
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
            await self._close_within(NORMAL_CLOSURE)

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
        await self._within(self._socket.send_text(json.dumps(hello)))
        try:
            message = await asyncio.wait_for(self._socket.receive(), timeout=self._hello_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            await self._close_within(CLOSE_BAD_MESSAGE, f"no hello within {self._hello_timeout_s:g}s")
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        text = message.get("text")
        try:
            hello_back = read_client_hello(parse(text) if isinstance(text, str) else None)
            encoding = negotiate(offered, hello_back)
        except ProtocolError as exc:
            await self._close_within(exc.code, exc.reason)
            return None
        # Which encoding a viewer got, and why, is what "only JPEG, never H.264" comes down to.
        logger.info(
            "a viewer of %s streams %s: it decodes %s, and %s is offered",
            instance.udid,
            encoding,
            ", ".join(hello_back["encodings"]),
            ", ".join(offered),
        )
        await self._within(self._socket.send_text(json.dumps(stream_start(encoding))))
        return encoding

    async def _close(self, code: int, reason: str) -> None:
        await self._close_within(code, reason)

    async def _within(self, sending: Awaitable[None]) -> None:
        """A send or close that finishes before the deadline; a viewer that takes nothing for that long is gone.

        The send is its own task, waited on and cancelled here rather than through ``asyncio.wait_for``: on Python
        3.10 that can lose a cancellation arriving as the send finishes, and a send that never notices it leaves the
        relay waiting for its own task -- the socket never closed, the viewer never let go.
        """
        send = asyncio.ensure_future(sending)
        try:
            done, _still_going = await asyncio.wait({send}, timeout=self._send_timeout_s)
        except BaseException:
            send.cancel()
            raise
        if not done:
            send.cancel()
            logger.info("a viewer of %s took nothing for %gs; letting it go", self._instance.udid, self._send_timeout_s)
            raise ViewerGone from None
        await send

    async def _close_within(self, code: int, reason: str | None = None) -> None:
        """Close the socket, and give up on a viewer that cannot take even that."""
        with contextlib.suppress(Exception):
            await self._within(self._socket.close(code=code, reason=reason))

    async def _send_event(self, event: Event) -> None:
        async with self._sending:
            await self._within(self._socket.send_text(json.dumps(event)))
        if event.get("type") == "status" and event.get("state") in (READY, STALLED):
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
                    await self._within(self._socket.send_bytes(frame(encoding, latest.data)))
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
            # What was read from the screen's pixels no longer holds once a person changes the screen.
            instance.text.hide()
            if self._person is None or self._person_session is not session:
                if self._person is not None:
                    await self._person.close()
                self._person = PersonInput(
                    session.input,
                    self._manager.simctl(instance),
                    instance.udid,
                    on_touch=lambda: self._manager.person_touched(instance),
                    typing=self._config.device_typing,
                    keyboard_is_us=self._manager.keyboard_is_us,
                )
                self._person_session = session
            try:
                await self._person.run(command)
            except (ConnectorError, SimctlError) as exc:
                logger.info("input to the simulator %s was not taken: %s", instance.udid, exc)
