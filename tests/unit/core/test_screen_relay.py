# SPDX-License-Identifier: Apache-2.0
"""One screen socket: a hello each way, the device's state and frames out, a person's touches in, and a clear end when
either side goes."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterable, Callable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, Crop, HidEvent, Shot
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.screen_relay import ScreenRelay, offered_encodings
from sim_mirror.protocol import (
    CLOSE_BAD_MESSAGE,
    CLOSE_STOPPED,
    CLOSE_UNSUPPORTED,
    MESSAGE_MAX_BYTES,
    SERVER,
    frame,
)
from sim_mirror.testing.fakes import FULL_CONTROL, JPEG, FakeConnector, FakeEngine
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope


class FakeSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.texts: list[dict[str, Any]] = []
        self.frames: list[bytes] = []
        self.closed: tuple[int, str | None] | None = None
        self.framed = asyncio.Event()

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(data)
        self.framed.set()

    async def send_text(self, data: str) -> None:
        self.texts.append(json.loads(data))

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = self.closed or (code, reason)

    def say(self, message: object) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "text": json.dumps(message)})

    def hello(self, *encodings: str) -> None:
        self.say({"type": "hello", "v": 1, "encodings": list(encodings or ("jpeg",))})

    def leave(self) -> None:
        self.incoming.put_nowait({"type": "websocket.disconnect"})

    def statuses(self) -> list[str]:
        return [text["state"] for text in self.texts if text["type"] == "status"]


async def until(predicate: Callable[[], object], tries: int = 500) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("never happened")


def relay(rig: DeviceRig, instance: DeviceInstance, socket: FakeSocket, **options: Any) -> asyncio.Task[None]:
    config = options.pop("config", None) or rig.config.get(instance.owner)
    return asyncio.ensure_future(ScreenRelay(socket, rig.manager, instance, config=config, **options).run())


def watch(rig: DeviceRig, instance: DeviceInstance, *encodings: str) -> tuple[FakeSocket, asyncio.Task[None]]:
    socket = FakeSocket()
    socket.hello(*encodings)
    return socket, relay(rig, instance, socket)


class ChangingScreen(FakeEngine):
    """A screen that never repeats, so frames keep coming while a test holds a send open."""

    shots = itertools.count()

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        await asyncio.sleep(0)
        return Shot(JPEG + bytes([next(self.shots) % 256]), max_width, 874)


# -- the hello -------------------------------------------------------------------------------------------------------


def test_h264_is_offered_first_where_the_connector_streams_it_and_the_settings_allow_it() -> None:
    config = SimConfig.defaults()
    assert offered_encodings(config, FULL_CONTROL) == ["h264", "jpeg"]
    assert offered_encodings(config, VIEW_ONLY) == ["jpeg"]
    assert offered_encodings(config.with_values(stream_encoding="jpeg"), FULL_CONTROL) == ["jpeg"]


async def test_a_viewer_hears_the_device_sees_its_frames_and_its_touches_reach_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket, running = watch(rig, instance, "webp", "jpeg")
    await asyncio.wait_for(socket.framed.wait(), 2)
    assert socket.texts[0] == {
        "type": "hello",
        "v": 1,
        "server": SERVER,
        "encodings": ["h264", "jpeg"],
        "connector": "idb",
        "capabilities": sorted(capability.value for capability in FULL_CONTROL),
        "fallback_reason": None,
    }
    assert socket.texts[1] == {"type": "stream", "encoding": "jpeg"}
    assert socket.texts[2]["type"] == "status" and socket.texts[2]["state"] == "ready"
    assert socket.frames[0] == frame("jpeg", JPEG) and instance.viewers == 1
    for noise in (
        {"type": "websocket.receive", "bytes": b"raw"},
        {"type": "websocket.receive", "text": "{nope"},
        {"type": "websocket.receive", "text": "x" * (MESSAGE_MAX_BYTES + 1)},
    ):
        socket.incoming.put_nowait(noise)
    socket.say({"type": "install", "path": "/tmp/Evil.app"})
    socket.say({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.5})
    socket.say({"type": "touch", "phase": "up", "nx": 0.5, "ny": 0.5})
    engine = rig.idb.engine
    await until(lambda: len(engine.hid_events) == 2)
    assert engine.hid_events[0].x == 201.0 and instance.person_touch_at == rig.clock.now
    socket.leave()
    await asyncio.wait_for(running, 2)
    assert instance.viewers == 0 and socket.closed == (1000, None)


@pytest.mark.parametrize(
    ("message", "closed"),
    [
        ({"type": "touch", "phase": "down"}, (CLOSE_BAD_MESSAGE, "the first message must be a hello")),
        ({"type": "hello", "v": 2, "encodings": ["jpeg"]}, (CLOSE_UNSUPPORTED, "protocol version 2 is not supported")),
        ({"type": "hello", "v": 1, "encodings": ["webp"]}, (CLOSE_UNSUPPORTED, "none of the encodings offered (webp)")),
    ],
)
async def test_a_viewer_that_does_not_say_hello_or_has_nothing_in_common_is_closed_with_why(
    tmp_path: Path, message: dict[str, Any], closed: tuple[int, str]
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FakeSocket()
    socket.say(message)
    await asyncio.wait_for(relay(rig, instance, socket), 2)
    assert socket.closed is not None and socket.closed[0] == closed[0] and socket.closed[1]
    assert socket.closed[1].startswith(closed[1])
    assert [text["type"] for text in socket.texts] == ["hello"] and instance.viewers == 0


async def test_a_viewer_that_decodes_only_what_is_not_offered_or_says_nothing_or_leaves_is_let_go(
    tmp_path: Path,
) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    jpeg_only = rig.config.get(instance.owner).with_values(stream_encoding="jpeg")
    socket = FakeSocket()
    socket.hello("h264")
    await asyncio.wait_for(relay(rig, instance, socket, config=jpeg_only), 2)
    assert socket.closed == (CLOSE_UNSUPPORTED, "the client decodes h264, and this server offers jpeg")
    silent = FakeSocket()
    await asyncio.wait_for(relay(rig, instance, silent, hello_timeout_s=0.01), 2)
    assert silent.closed == (CLOSE_BAD_MESSAGE, "no hello within 0.01s")
    raw = FakeSocket()
    raw.incoming.put_nowait({"type": "websocket.receive", "bytes": b"hello"})
    await asyncio.wait_for(relay(rig, instance, raw), 2)
    assert raw.closed is not None and raw.closed[0] == CLOSE_BAD_MESSAGE
    gone = FakeSocket()
    gone.leave()
    await asyncio.wait_for(relay(rig, instance, gone), 2)
    assert gone.closed is None and len(gone.texts) == 1 and instance.viewers == 0


async def test_a_view_only_mirror_offers_jpeg_and_takes_only_the_appearance(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", available=False))
    instance = await rig.up()
    socket, running = watch(rig, instance, "h264", "jpeg")
    await asyncio.wait_for(socket.framed.wait(), 2)
    hello = socket.texts[0]
    assert hello["encodings"] == ["jpeg"] and hello["connector"] == "simctl" and hello["fallback_reason"]
    assert socket.texts[1] == {"type": "stream", "encoding": "jpeg"}
    socket.say({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.5})
    socket.say({"type": "appearance", "mode": "dark"})
    await until(lambda: ("simctl", "ui", instance.udid, "appearance", "dark") in rig.argv())
    assert rig.simctl_connector.engine.hid_events == []
    socket.leave()
    await asyncio.wait_for(running, 2)


# -- frames, events and input ----------------------------------------------------------------------------------------


async def test_a_persons_touches_follow_the_device_to_a_connector_attached_again(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    first = rig.idb.engine
    socket.say({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.5})
    socket.say({"type": "touch", "phase": "up", "nx": 0.5, "ny": 0.5})
    await until(lambda: len(first.hid_events) == 2)
    rig.idb.engine = second = FakeEngine()
    instance.session = await rig.idb.attach(instance.udid, rig.config.get(instance.owner))
    socket.say({"type": "touch", "phase": "down", "nx": 0.25, "ny": 0.25})
    socket.say({"type": "touch", "phase": "up", "nx": 0.25, "ny": 0.25})
    await until(lambda: len(second.hid_events) == 2)
    assert len(first.hid_events) == 2 and second.hid_events[0].x == 100.5
    socket.leave()
    await asyncio.wait_for(running, 2)


async def test_a_viewer_on_a_booting_device_waits_for_it_and_hears_when_it_is_ready(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", hold=True))
    rig.idb.engine.chunks = [b"\x00\x00\x00\x01\x67sps"]
    instance = await rig.manager.ensure(scope())
    socket, running = watch(rig, instance, "h264", "jpeg")
    await until(lambda: socket.statuses())
    assert socket.texts[1] == {"type": "stream", "encoding": "h264"}
    socket.say({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.5})
    await asyncio.sleep(0.01)
    assert socket.statuses() == ["booting"] and not socket.frames and not rig.idb.engine.hid_events
    assert rig.idb.release is not None and instance.task is not None
    rig.idb.release.set()
    await instance.task
    await asyncio.wait_for(socket.framed.wait(), 2)
    assert socket.frames[0] == frame("h264", b"\x00\x00\x00\x01\x67sps")
    assert socket.statuses() == ["booting", "ready"]
    socket.leave()
    await asyncio.wait_for(running, 2)


async def test_a_device_that_is_ended_closes_its_viewer_and_says_so(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    await rig.manager.stop(scope())
    await asyncio.wait_for(running, 2)
    assert socket.closed == (CLOSE_STOPPED, "the simulator stopped") and socket.statuses()[-1] == "stopped"


async def test_a_device_that_fails_tells_its_viewer_before_the_screen_goes(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    rig.idb.dead.add(instance.udid)
    rig.idb.fail = ConnectorUnavailable("idb_companion exited as it started (exit 1)")
    await rig.manager.reap()
    assert instance.recovery is not None
    await asyncio.wait_for(instance.recovery, 2)
    await asyncio.wait_for(running, 2)
    assert socket.statuses()[-1] == "failed" and socket.closed is not None


async def test_a_ready_device_with_no_frame_hub_or_no_session_ends_or_ignores_quietly(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    hub, instance.hub = instance.hub, None
    socket, running = watch(rig, instance)
    await asyncio.wait_for(running, 2)
    assert socket.closed == (1000, None) and not socket.frames
    instance.hub = hub
    session, instance.session = instance.session, None
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    socket.say({"type": "button", "name": "home"})
    await asyncio.sleep(0.01)
    assert rig.idb.engine.hid_events == []
    instance.session = session
    socket.leave()
    await asyncio.wait_for(running, 2)


async def test_events_still_waiting_when_the_viewer_goes_are_sent_before_it_closes(tmp_path: Path) -> None:
    class SlowViewer(FakeSocket):
        async def send_text(self, data: str) -> None:
            self.texts.append(json.loads(data))
            if len(self.texts) == 3:
                # Slow to take the device's state: what comes next waits in the relay's queue.
                await asyncio.Event().wait()

    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = SlowViewer()
    socket.hello()
    running = relay(rig, instance, socket)
    await until(lambda: len(socket.texts) == 3)
    instance.events.publish({"type": "agent", "id": "a1", "phase": "intent"})
    socket.leave()
    await asyncio.wait_for(running, 2)
    assert socket.texts[-1] == {"type": "agent", "id": "a1", "phase": "intent"}
    assert socket.closed == (1000, None) and not socket.frames


# -- a viewer that takes nothing -------------------------------------------------------------------------------------
# A page the browser froze keeps its socket open but takes nothing it is sent, so a send to it never finishes.


async def test_a_viewer_that_stops_taking_frames_is_let_go_and_no_longer_holds_its_device(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class FrozenViewer(FakeSocket):
        async def send_bytes(self, data: bytes) -> None:
            self.frames.append(data)
            self.framed.set()
            await asyncio.Event().wait()

    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FrozenViewer()
    socket.hello()
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(relay(rig, instance, socket, send_timeout_s=0.05), 2)
    assert instance.viewers == 0 and socket.closed == (1000, None) and len(socket.frames) == 1
    assert f"a viewer of {instance.udid} took nothing for 0.05s" in caplog.text


@pytest.mark.parametrize("frozen_at", [1, 3], ids=["its hello", "the device's state"])
async def test_a_viewer_frozen_before_it_sees_the_screen_is_let_go_all_the_same(tmp_path: Path, frozen_at: int) -> None:
    class FrozenViewer(FakeSocket):
        async def send_text(self, data: str) -> None:
            self.texts.append(json.loads(data))
            if len(self.texts) == frozen_at:
                await asyncio.Event().wait()

    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FrozenViewer()
    socket.hello()
    await asyncio.wait_for(relay(rig, instance, socket, send_timeout_s=0.05), 2)
    assert instance.viewers == 0 and socket.closed == (1000, None) and not socket.frames


async def test_closing_a_frozen_viewer_holds_up_neither_the_device_stopping_nor_the_relay(tmp_path: Path) -> None:
    class FrozenViewer(FakeSocket):
        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            await super().close(code, reason)
            await asyncio.Event().wait()

    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FrozenViewer()
    socket.hello()
    running = relay(rig, instance, socket, send_timeout_s=0.05)
    await asyncio.wait_for(socket.framed.wait(), 2)
    await asyncio.wait_for(rig.manager.stop(scope()), 2)
    await asyncio.wait_for(running, 2)
    assert socket.closed == (CLOSE_STOPPED, "the simulator stopped") and instance.viewers == 0


async def test_a_relay_whose_send_is_in_flight_still_ends_when_its_viewer_goes(tmp_path: Path) -> None:
    # The frame task is cancelled in the middle of a send when the viewer leaves. The relay must end all the same: a
    # send that does not notice its cancellation leaves `run()` waiting for that task for ever, its socket never
    # closed and its viewer never let go. Shielded here, so a relay that cannot end fails this test rather than hangs.
    class SlowSocket(FakeSocket):
        async def send_text(self, data: str) -> None:
            # A write buffer that takes its time, so a frame is mid-send when the viewer goes.
            await asyncio.sleep(0.05)
            await super().send_text(data)

    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=ChangingScreen()))
    instance = await rig.up()
    socket = SlowSocket()
    socket.hello()
    running = relay(rig, instance, socket)
    await asyncio.wait_for(socket.framed.wait(), 2)
    instance.events.publish({"type": "agent", "id": "a1", "phase": "intent"})
    await until(lambda: len(socket.texts) == 4)
    sent = len(socket.frames)
    await until(lambda: len(socket.frames) > sent + 1)
    socket.leave()
    await asyncio.wait_for(asyncio.shield(running), 2)
    assert running.done() and instance.viewers == 0 and socket.closed is not None


async def test_a_frame_never_goes_out_while_an_event_is_still_going_out(tmp_path: Path) -> None:
    # One send at a time: a frame that starts while an event is still going out interleaves the two for the viewer.
    class FullSocket(FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.texting = False
            self.overlapped = False

        async def send_bytes(self, data: bytes) -> None:
            self.overlapped = self.overlapped or self.texting
            await super().send_bytes(data)

        async def send_text(self, data: str) -> None:
            self.texting = True
            try:
                # A write buffer that takes its time to empty, while new frames keep coming.
                await asyncio.sleep(0.05)
                await super().send_text(data)
            finally:
                self.texting = False

    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=ChangingScreen()))
    instance = await rig.up()
    socket = FullSocket()
    socket.hello()
    running = relay(rig, instance, socket)
    await asyncio.wait_for(socket.framed.wait(), 2)
    instance.events.publish({"type": "agent", "id": "a1", "phase": "intent"})
    await until(lambda: len(socket.texts) == 4)
    sent = len(socket.frames)
    await until(lambda: len(socket.frames) > sent + 1)
    socket.leave()
    # Shielded: a relay that cannot end fails this test rather than hanging it (and the whole run) with it.
    await asyncio.wait_for(asyncio.shield(running), 2)
    assert socket.overlapped is False


async def test_input_the_device_refuses_is_logged_and_the_viewer_stays(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Refusing(FakeEngine):
        async def hid(self, events: AsyncIterable[HidEvent]) -> None:
            raise ConnectorError("sending input failed: companion gone")

    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=Refusing()))
    instance = await rig.up()
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    with caplog.at_level(logging.INFO):
        socket.say({"type": "button", "name": "home"})
        await until(lambda: "was not taken" in caplog.text)
    assert not running.done()
    socket.leave()
    await asyncio.wait_for(running, 2)
    assert Capability.INPUT_BUTTON in instance.capabilities


async def test_a_device_left_stalled_after_its_last_viewer_went_streams_again_to_the_next_one(tmp_path: Path) -> None:
    class Flaky(FakeEngine):
        failing = False

        async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
            if self.failing:
                await asyncio.sleep(0)
                raise ConnectorError("screen went away")
            return await super().screenshot(max_width=max_width, quality=quality, crop=crop)

    engine = Flaky()
    rig = DeviceRig(tmp_path, idb=FakeConnector("idb", engine=engine))
    instance = await rig.up()
    hub = instance.hub
    assert hub is not None
    engine.failing = True
    watcher = hub.subscribe("jpeg")
    await until(lambda: instance.state == "stalled")
    hub.unsubscribe(watcher)
    await until(lambda: "jpeg" not in hub._sources)
    engine.failing = False
    socket, running = watch(rig, instance)
    await asyncio.wait_for(socket.framed.wait(), 2)
    assert socket.statuses()[0] == "stalled" and instance.state == "ready"
    socket.leave()
    await asyncio.wait_for(running, 2)


async def test_a_device_ended_during_the_hello_closes_that_socket_and_a_late_one_hears_it_ended(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FakeSocket()
    running = relay(rig, instance, socket, hello_timeout_s=0.05)
    await until(lambda: instance.viewers == 1)
    await rig.manager.stop(scope())
    await asyncio.wait_for(running, 2)
    assert socket.closed == (CLOSE_STOPPED, "the simulator stopped") and instance.viewers == 0
    late = FakeSocket()
    late.hello()
    await asyncio.wait_for(relay(rig, instance, late), 2)
    assert late.closed == (CLOSE_STOPPED, "the simulator stopped") and late.texts == []


async def test_a_socket_is_known_to_the_manager_as_the_scope_that_opened_it(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    instance = await rig.up()
    socket = FakeSocket()
    socket.hello()
    config = rig.config.get(instance.owner)
    running = asyncio.ensure_future(
        ScreenRelay(socket, rig.manager, instance, config=config, scope=scope("tp-1")).run()
    )
    await until(lambda: instance.viewers == 1)
    assert list(instance.sockets.values()) == ["tp-1"]
    socket.leave()
    await asyncio.wait_for(running, 2)
