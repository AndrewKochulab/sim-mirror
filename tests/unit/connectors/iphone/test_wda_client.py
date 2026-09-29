# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent's HTTP over a device's cable: one connection a request, its errors said, its session kept."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.iphone.wda_client import (
    ANSWER_MAX,
    HTTP_PORT,
    WdaClient,
    WdaError,
    answer_of,
    exchange,
    usbmux_opener,
)
from sim_mirror.platform.usbmux import UsbDevice, UsbmuxError
from sim_mirror.testing.fakes import PHONE_UDID
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.wda import FakeWda


@pytest.fixture
async def wda() -> AsyncIterator[FakeWda]:
    with short_run_dir() as folder:
        fake = await FakeWda(folder).serve()
        try:
            yield fake
        finally:
            await fake.stop()


async def test_a_request_is_json_over_its_own_connection_and_answers_its_value(wda: FakeWda) -> None:
    client = WdaClient(wda.opener())
    assert await client.status() == {"ready": True, "build": {"version": "16.12.10"}}
    assert await client.call("POST", "/wda/homescreen", {"x": 1}) is None
    assert wda.requests == [("GET", "/status", None), ("POST", "/wda/homescreen", {"x": 1})]


async def test_a_session_is_made_once_and_again_when_webdriveragent_forgot_it(wda: FakeWda) -> None:
    client = WdaClient(wda.opener())
    assert await client.in_session("GET", "/orientation") == "PORTRAIT"
    assert await client.in_session("GET", "/orientation") == "PORTRAIT" and wda.sessions == 1
    wda.session = None  # WebDriverAgent restarted
    assert await client.in_session("GET", "/orientation") == "PORTRAIT" and wda.sessions == 2
    wda.fail("GET", "/orientation", "unknown error", "no orientation\nat line 1")
    with pytest.raises(WdaError, match=r"WebDriverAgent refused it: no orientation$") as refused:
        await client.in_session("GET", "/orientation")
    assert refused.value.error == "unknown error" and refused.value.status == 500


async def test_a_session_webdriveragent_will_not_make_or_forgets_twice_is_refused(wda: FakeWda) -> None:
    wda.answers[("POST", "/session")] = (200, {"value": {}})
    with pytest.raises(WdaError, match="made no session"):
        await WdaClient(wda.opener()).session()
    wda.answers[("POST", "/session")] = (200, {"sessionId": "gone", "value": {}})
    with pytest.raises(WdaError, match="Session does not exist: gone"):
        await WdaClient(wda.opener()).in_session("GET", "/orientation")


@pytest.mark.parametrize(
    ("status", "data", "said"),
    [
        (200, b"<html>", "not JSON"),
        (200, b"[1]", "not a WebDriver answer"),
        (404, b"{}", "refused it \\(HTTP 404\\)"),
        (500, b'{"value": {"error": "no such element"}}', "refused it: no such element"),
    ],
)
def test_an_answer_that_is_not_a_success_says_why(status: int, data: bytes, said: str) -> None:
    with pytest.raises(WdaError, match=said):
        answer_of(status, data)
    assert answer_of(200, b"") == {}


async def serving(folder: Path, answer: bytes, *, hold: bool = False) -> asyncio.AbstractServer:
    async def reply(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(answer)
        await writer.drain()
        if hold:
            await asyncio.sleep(0.3)
        writer.close()

    return await asyncio.start_unix_server(reply, path=str(folder / "s.sock"))


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (b"garbage\r\n\r\n", "answered what is not HTTP"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort", "stopped answering"),
        (f"HTTP/1.1 200 OK\r\nContent-Length: {ANSWER_MAX + 1}\r\n\r\n".encode(), "larger than 32 MB"),
    ],
)
async def test_an_answer_that_is_not_http_or_ends_early_is_refused(answer: bytes, said: str) -> None:
    with short_run_dir() as folder:
        server = await serving(folder, answer)

        async def open_port(port: int) -> Any:
            return await asyncio.open_unix_connection(str(folder / "s.sock"))

        try:
            with pytest.raises(WdaError, match=said):
                await exchange(open_port, HTTP_PORT, "GET", "/status")
        finally:
            server.close()


async def test_an_answer_without_a_length_is_read_to_its_end_and_a_slow_one_times_out() -> None:
    with short_run_dir() as folder:
        server = await serving(folder, b'HTTP/1.1 200 OK\r\n\r\n{"value": 7}')

        async def open_port(port: int) -> Any:
            return await asyncio.open_unix_connection(str(folder / "s.sock"))

        assert await WdaClient(open_port).call("GET", "/status") == 7
        server.close()
        slow = await serving(folder, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n", hold=True)
        try:
            with pytest.raises(WdaError, match="did not answer GET /status in time"):
                await WdaClient(open_port, timeout_s=0.05).call("GET", "/status")
            assert await WdaClient(open_port, timeout_s=0.05).status() is None
        finally:
            slow.close()


class Mux:
    """usbmuxd as a test says: the device plugged in or not, and a socket to its port."""

    def __init__(self, plugged: bool, folder: Path) -> None:
        self.plugged = plugged
        self.folder = folder
        self.connected: list[tuple[int, int]] = []

    def find(self, udid: str) -> UsbDevice | None:
        return UsbDevice(3, udid, "USB") if self.plugged else None

    def connect(self, device_id: int, port: int) -> socket.socket:
        self.connected.append((device_id, port))
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self.folder / f"wda-{port}.sock"))
        return sock


async def test_the_cable_reaches_webdriveragent_on_the_devices_own_ports(wda: FakeWda) -> None:
    mux = Mux(True, wda.folder)
    assert await WdaClient(usbmux_opener(PHONE_UDID, mux)).status() is not None  # type: ignore[arg-type]
    assert mux.connected == [(3, HTTP_PORT)]
    with pytest.raises(WdaError, match=r"cannot be reached: .* is not plugged in by cable"):
        await usbmux_opener(PHONE_UDID, Mux(False, wda.folder))(HTTP_PORT)  # type: ignore[arg-type]

    class Broken(Mux):
        def find(self, udid: str) -> UsbDevice | None:
            raise UsbmuxError("usbmuxd cannot be reached")

    with pytest.raises(WdaError, match="usbmuxd cannot be reached"):
        await usbmux_opener(PHONE_UDID, Broken(True, wda.folder))(HTTP_PORT)  # type: ignore[arg-type]
    assert isinstance(usbmux_opener(PHONE_UDID), object)
