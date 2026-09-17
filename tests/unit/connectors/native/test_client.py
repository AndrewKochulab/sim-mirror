# SPDX-License-Identifier: Apache-2.0
"""The native helper's client: a real socket to a helper standing in for the Swift one, every request and failure."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sim_mirror.connectors.base import ConnectorError, Crop, HidEvent, Screen, Shot
from sim_mirror.connectors.native import client as client_module
from sim_mirror.connectors.native import wire
from sim_mirror.connectors.native.client import Hello, HelperClient
from sim_mirror.testing.fakes import JPEG, SCREEN, FakeEngine
from sim_mirror.testing.native import FakeHelper, short_run_dir


@pytest.fixture
async def socket_path() -> AsyncIterator[Path]:
    with short_run_dir() as folder:
        yield folder / "h.sock"


async def serving(path: Path, helper: FakeHelper | None = None) -> tuple[FakeHelper, HelperClient]:
    helper = helper or FakeHelper()
    await helper.serve(path)
    return helper, HelperClient.at(str(path))


async def events(*items: HidEvent) -> AsyncIterator[HidEvent]:
    for item in items:
        yield item


async def test_a_hello_says_the_versions_the_screen_and_how_input_goes(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        hello = await client.hello()
        assert hello == Hello(
            wire=1, version=helper.version, core_simulator="1171.7", hid="dtuhid", reasons=(), screen=SCREEN
        )
        assert await client.describe() == SCREEN
    finally:
        await client.close()
        await helper.stop()


async def test_screenshots_and_the_screen_come_back_as_asked(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        crop = Crop(1.0, 2.0, 30.0, 40.0)
        shots = await asyncio.gather(
            client.screenshot(max_width=400, quality=70), client.screenshot(max_width=160, quality=40, crop=crop)
        )
        assert shots == [Shot(JPEG, 402, 874), Shot(JPEG, 402, 874)]
        assert sorted(helper.engine.screenshots, key=lambda call: call[0]) == [(160, 40, crop), (400, 70, None)]
        document = await client.accessibility()
        assert document == helper.engine.document
    finally:
        await client.close()
        await helper.stop()


async def test_input_goes_one_event_at_a_time_in_order(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        sent = (
            HidEvent.touch("down", 10.5, 20),
            HidEvent.touch("up", 10.5, 20),
            HidEvent.press("home", "down"),
            HidEvent.key(40, "up"),
        )
        await client.hid(events(*sent))
        assert helper.engine.hid_events == list(sent)
        assert [request["events"] for request in helper.requests] == [
            [{"kind": "touch", "phase": "down", "x": 10.5, "y": 20.0}],
            [{"kind": "touch", "phase": "up", "x": 10.5, "y": 20.0}],
            [{"kind": "button", "phase": "down", "button": "home"}],
            [{"kind": "key", "phase": "up", "code": 40}],
        ]
    finally:
        await client.close()
        await helper.stop()


async def test_a_stream_is_the_helpers_chunks_on_its_own_connection(socket_path: Path) -> None:
    engine = FakeEngine()
    engine.chunks = [b"\x00\x00\x00\x01\x67", b"\x00\x00\x00\x01\x41"]
    helper, client = await serving(socket_path, FakeHelper(engine))
    try:
        stream = client.h264(fps=30, scale=0.75, key_frame_s=1.0, bitrate=3_000_000)
        assert [await anext(stream), await anext(stream)] == engine.chunks
        await stream.aclose()
        assert helper.requests == [{"op": "stream", "fps": 30, "scale": 0.75, "key_frame_s": 1.0, "bitrate": 3000000}]
    finally:
        await client.close()
        await helper.stop()


async def test_a_stream_that_ends_or_fails_says_so(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        helper.raw_answers = [
            wire.encode(wire.Frame(wire.CHUNK, 1, b"", b"unit")) + wire.encode(wire.Frame(wire.REPLY, 1, b"{}"))
        ]
        assert [chunk async for chunk in client.h264(fps=1, scale=1, key_frame_s=1, bitrate=1)] == [b"unit"]
        failure = json.dumps({"message": "the H.264 encoder failed (-12902)"}).encode()
        helper.raw_answers = [wire.encode(wire.Frame(wire.FAILURE, 1, failure))]
        with pytest.raises(ConnectorError, match=r"the video stream failed: the H.264 encoder failed \(-12902\)"):
            async for _chunk in client.h264(fps=1, scale=1, key_frame_s=1, bitrate=1):
                pass
    finally:
        await client.close()
        await helper.stop()


async def test_what_the_helper_refuses_it_says_why(socket_path: Path) -> None:
    engine = FakeEngine(describe_error=ConnectorError("the simulator is not booted"))
    engine.accessibility_errors = [ConnectorError("no frontmost application")]
    helper, client = await serving(socket_path, FakeHelper(engine))
    try:
        with pytest.raises(ConnectorError, match="the simulator is not booted"):
            await client.describe()
        with pytest.raises(ConnectorError, match="no frontmost application"):
            await client.accessibility()
        helper.raw_answers = [wire.encode(wire.Frame(wire.FAILURE, 3, b""))]
        with pytest.raises(ConnectorError, match="the native helper refused screenshot"):
            await client.screenshot(max_width=1, quality=1)
    finally:
        await client.close()
        await helper.stop()


async def test_answers_that_cannot_be_read_are_failures(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        helper.raw_answers = [wire.encode(wire.Frame(wire.REPLY, 1, b'{"wire": 1}'))]
        with pytest.raises(ConnectorError, match="hello cannot be read"):
            await client.hello()
        helper.raw_answers = [wire.encode(wire.Frame(wire.REPLY, 2, b'{"width_px": "wide"}'))]
        with pytest.raises(ConnectorError, match="describing the device failed: the answer cannot be read"):
            await client.describe()
        # An answer to no request is passed over; a screenshot's answer without a size is an empty one.
        helper.raw_answers = [
            wire.encode(wire.Frame(wire.REPLY, 99, b"{}")) + wire.encode(wire.Frame(wire.REPLY, 3, b"{}"))
        ]
        assert await client.screenshot(max_width=1, quality=1) == Shot(b"", 0, 0)
    finally:
        await client.close()
        await helper.stop()


async def test_a_helper_that_goes_away_did_not_answer_and_the_next_call_connects_again(
    socket_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper, client = await serving(socket_path)
    try:
        helper.raw_answers = [b"\x00\x00\x00\x09\x07\x00\x00\x00\x01\x00\x00\x00\x00"]
        with pytest.raises(ConnectorError, match="describing the device failed: the native helper did not answer"):
            await client.describe()
        assert await client.describe() == SCREEN
        monkeypatch.setattr(client_module, "DESCRIBE_TIMEOUT_S", 0.01)
        helper.raw_answers = [b""]
        with pytest.raises(ConnectorError, match="did not answer"):
            await client.describe()
        await helper.stop()
        with pytest.raises(ConnectorError, match="sending input failed: the native helper did not answer"):
            await client.hid(events(HidEvent.key(4, "down")))
    finally:
        await client.close()
        await helper.stop()


async def test_a_client_let_go_of_refuses_every_call(socket_path: Path) -> None:
    helper, client = await serving(socket_path)
    try:
        await client.hid(events(HidEvent.key(4, "down")))
        await client.describe()
    finally:
        await client.close()
        await helper.stop()
    with pytest.raises(ConnectorError, match="has been let go of"):
        await client.describe()
    with pytest.raises(ConnectorError, match="has been let go of"):
        await anext(client.h264(fps=1, scale=1, key_frame_s=1, bitrate=1))
    await client.close()


def test_a_screen_is_read_from_its_pixels_and_points() -> None:
    assert client_module._screen({"width_px": 1, "height_px": 2, "width_pt": 3, "height_pt": 4, "scale": 2}) == Screen(
        1, 2, 3, 4, 2.0
    )


async def test_a_helper_that_closes_the_connection_ends_what_waited_on_it(socket_path: Path) -> None:
    engine = FakeEngine()
    helper, client = await serving(socket_path, FakeHelper(engine))
    try:
        stream = client.h264(fps=30, scale=1, key_frame_s=1, bitrate=1_000_000)
        assert await anext(stream) == engine.chunks[0]
        helper.raw_answers = [b""]
        waiting = asyncio.ensure_future(client.describe())
        while not any(request.get("op") == "describe" for request in helper.requests):
            await asyncio.sleep(0.01)
        await helper.stop()
        with pytest.raises(ConnectorError, match="the native helper closed the connection"):
            await waiting
        assert [chunk async for chunk in stream] == []
    finally:
        await client.close()
        await helper.stop()
