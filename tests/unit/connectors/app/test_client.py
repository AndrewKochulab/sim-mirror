# SPDX-License-Identifier: Apache-2.0
"""Asking an app for its hierarchy over loopback: the request it is sent, and every way an app can fail to answer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.connectors.app import client, wire
from sim_mirror.connectors.app.client import fetch_hierarchy
from sim_mirror.connectors.app.discovery import AppListing
from sim_mirror.connectors.app.errors import AppInactive, AppRefused, AppSdkError, AppTimedOut, AppUnreachable
from sim_mirror.testing.app_sdk import ANSWERS, SECRET, FakeAppSdk, app_hierarchy, app_node


def listing_for(app: FakeAppSdk, **more: Any) -> AppListing:
    values: dict[str, Any] = {
        "path": Path("/nowhere/app.json"), "modified_ns": 1, "protocol": 1, "sdk_version": "1.0.0",
        "bundle_id": app.bundle_id, "name": app.name, "pid": app.pid, "port": app.port, "active": True,
        "secret": app.secret,
    }  # fmt: skip
    values.update(more)
    return AppListing(**values)


async def test_an_app_in_front_answers_its_hierarchy_to_a_request_carrying_its_secret_in_a_header() -> None:
    document = app_hierarchy(app_node("text", "Hello"))
    async with FakeAppSdk(document) as app:
        answer = await fetch_hierarchy(listing_for(app), max_nodes=500, timeout_s=5)
    assert answer == document
    (request,) = app.requests
    assert request.line == "GET /v1/hierarchy?max_nodes=500 HTTP/1.1"
    assert request.headers == {
        "host": f"127.0.0.1:{app.port}",
        "authorization": f"Bearer {SECRET}",
        "accept": "application/json",
        "user-agent": f"sim-mirror/{__version__}",
        "connection": "close",
    }
    assert SECRET not in request.line


async def test_an_answer_without_a_length_is_read_to_its_end() -> None:
    async with FakeAppSdk() as app:
        app.answer = "unsized"
        assert await fetch_hierarchy(listing_for(app), max_nodes=1, timeout_s=5) == app.document


@pytest.mark.parametrize(
    ("answer", "error", "says"),
    [
        ("inactive", AppInactive, "AppSDK is not in front"),
        ("refuse", AppRefused, "AppSDK refused to share its view hierarchy (503 busy)"),
        ("hang_up", AppUnreachable, "AppSDK hung up before answering"),
        ("not_json", AppSdkError, "AppSDK answered with something other than JSON"),
        ("not_object", AppSdkError, "AppSDK answered with something other than a view hierarchy"),
        ("too_large", AppSdkError, f"AppSDK answered with more than {wire.RESPONSE_MAX_BYTES} bytes"),
        ("unsized_too_large", AppSdkError, f"AppSDK answered with more than {wire.RESPONSE_MAX_BYTES} bytes"),
        ("chunked", AppSdkError, "AppSDK answered in chunks, which the app SDK protocol does not use"),
        ("long_headers", AppSdkError, f"AppSDK answered with headers longer than {wire.HEADERS_MAX_BYTES} bytes"),
        ("not_http", AppSdkError, "AppSDK did not answer in HTTP"),
        ("bad_length", AppSdkError, "AppSDK answered with a length that is not a number"),
    ],
)
async def test_every_way_an_app_can_fail_to_answer_says_why_and_never_its_secret(
    answer: str, error: type[AppSdkError], says: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with FakeAppSdk() as app:
        app.answer = answer
        with pytest.raises(error) as raised:
            await fetch_hierarchy(listing_for(app), max_nodes=10, timeout_s=5)
    assert str(raised.value) == says
    assert SECRET not in str(raised.value) and SECRET not in caplog.text


def test_every_answer_the_fake_app_gives_is_tried() -> None:
    tried = {"hierarchy", "unsized", "hang"}
    tried |= {"inactive", "refuse", "hang_up", "not_json", "not_object", "too_large", "unsized_too_large", "chunked"}
    tried |= {"long_headers", "not_http", "bad_length"}
    assert tried == ANSWERS


async def test_a_wrong_secret_is_refused_by_the_app() -> None:
    async with FakeAppSdk() as app:
        with pytest.raises(AppRefused) as raised:
            await fetch_hierarchy(listing_for(app, secret="x" * 43), max_nodes=10, timeout_s=5)
    assert raised.value.status == 401 and str(raised.value).endswith("(401 unauthorized)")


async def test_a_refusal_without_an_error_body_still_says_its_status() -> None:
    async with FakeAppSdk() as app:
        made = listing_for(app)
    for body in (b"", b"[]", b'{"error": {"code": "has spaces"}}', b'{"error": "nope"}'):
        refused = client._refusal(418, body, made)
        assert isinstance(refused, AppRefused) and str(refused).endswith("(418)")


async def test_an_app_that_does_not_answer_in_time_times_out() -> None:
    async with FakeAppSdk() as app:
        app.answer = "hang"
        with pytest.raises(AppTimedOut, match=r"\AAppSDK did not answer within 50 ms\Z"):
            await fetch_hierarchy(listing_for(app), max_nodes=10, timeout_s=0.05)


async def test_nothing_listening_where_the_app_said_is_an_app_that_has_gone() -> None:
    app = await FakeAppSdk().start()
    listing = listing_for(app)
    await app.close()
    with pytest.raises(AppUnreachable, match="AppSDK is not listening any more"):
        await fetch_hierarchy(listing, max_nodes=10, timeout_s=5)


async def test_a_connection_is_only_ever_made_to_loopback_within_the_header_limit() -> None:
    asked: list[tuple[Any, ...]] = []

    async def connect(*args: Any, **kwargs: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        asked.append((*args, kwargs))
        raise ConnectionRefusedError

    app = await FakeAppSdk().start()
    listing = listing_for(app)
    await app.close()
    with pytest.raises(AppUnreachable):
        await fetch_hierarchy(listing, max_nodes=10, timeout_s=5, connect=connect)
    assert asked == [("127.0.0.1", listing.port, {"limit": wire.HEADERS_MAX_BYTES})]


async def test_the_fake_app_needs_starting_before_it_has_a_port_and_writes_the_listing_an_app_would() -> None:
    unstarted = FakeAppSdk()
    with pytest.raises(AssertionError, match="start the fake app first"):
        _ = unstarted.port
    await unstarted.close()
    async with FakeAppSdk(pid=7) as app:
        assert app.listing("UDID", active=False) == {
            "protocol": 1, "sdk_version": "1.0.0", "device_udid": "UDID", "bundle_id": app.bundle_id,
            "name": "AppSDK", "pid": 7, "port": app.port, "secret": SECRET, "active": False,
            "started_at": "2026-09-17T08:00:00.000Z",
        }  # fmt: skip
        reader, writer = await asyncio.open_connection(wire.HOST, app.port)
        writer.close()
        await writer.wait_closed()
        assert await reader.read() == b""
    assert app.requests == []


def test_a_header_line_without_a_name_is_passed_over() -> None:
    app = FakeAppSdk()
    made = AppListing(Path("/x.json"), 1, 1, "1.0.0", app.bundle_id, "AppSDK", 1, 1024, True, SECRET)
    assert client._head(b"HTTP/1.1 200 OK\r\nnonsense\r\nContent-Length: 2", made) == (200, {"content-length": "2"})
