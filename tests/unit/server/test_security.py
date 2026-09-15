# SPDX-License-Identifier: Apache-2.0
"""A local server used only by the pages it chose: the Host allowlist, exact origins, CORS for listed origins, and the
headers that keep its pages from being framed or its URLs from leaking -- for requests and sockets alike."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.types import Message, Receive, Send
from starlette.websockets import WebSocket

from sim_mirror.protocol import CLOSE_FORBIDDEN
from sim_mirror.server.security import SecurityMiddleware, SiteRules, origin_of
from sim_mirror.testing.asgi import HOST, AsgiSocket

RULES = SiteRules(
    port=7466,
    allowed_origins=("https://HOST.example", "not an origin"),
    frame_ancestors=("https://frame.example/page", "javascript:alert(1)"),
)
OWN = "http://127.0.0.1:7466"
LISTED = "https://host.example"
FOREIGN = "https://evil.example"


async def page(request: Request) -> JSONResponse:
    return JSONResponse({"method": request.method})


async def socket(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.close()


APP = SecurityMiddleware(
    Starlette(routes=[Route("/x", page, methods=["GET", "POST"]), WebSocketRoute("/ws", socket)]), lambda: RULES
)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url=f"http://{HOST}")


@pytest.mark.parametrize(
    ("value", "origin"),
    [
        (None, None),
        ("", None),
        ("null", None),
        ("file:///Users/me/page.html", None),
        ("ftp://files.example", None),
        ("http://host.example:bad", None),
        ("HTTP://LocalHost:7466/viewer/x?y=1", "http://localhost:7466"),
        ("https://host.example:443", "https://host.example"),
        ("http://host.example:80", "http://host.example"),
        ("http://[::1]:7466", "http://[::1]:7466"),
    ],
)
def test_an_origin_is_its_scheme_host_and_port_and_nothing_else_is_one(value: str | None, origin: str | None) -> None:
    assert origin_of(value) == origin


def test_a_server_answers_for_its_loopback_names_and_listed_origins_only() -> None:
    assert RULES.host_allowed("127.0.0.1:7466") and RULES.host_allowed(" LOCALHOST:7466 ")
    assert (
        not RULES.host_allowed("127.0.0.1:7467")
        and not RULES.host_allowed("evil.example")
        and not RULES.host_allowed(None)
    )
    assert RULES.origin_allowed(OWN) and RULES.origin_allowed("http://localhost:7466") and RULES.origin_allowed(LISTED)
    assert not RULES.origin_allowed(FOREIGN) and not RULES.origin_allowed("null") and not RULES.origin_allowed(None)
    assert RULES.cors_allowed(LISTED) and not RULES.cors_allowed(OWN)
    assert dict(RULES.headers()) == {
        b"content-security-policy": b"frame-ancestors 'self' https://frame.example",
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
    }


async def test_a_request_for_a_host_the_server_does_not_answer_for_is_refused() -> None:
    async with client() as http:
        refused = await http.get("/x", headers={"host": "rebound.example:7466"})
    assert refused.status_code == 400 and refused.json() == {
        "ok": False,
        "error": "this server does not answer for that host",
    }
    assert refused.headers["content-security-policy"] == "frame-ancestors 'self' https://frame.example"


async def test_a_page_reads_answers_only_when_listed_and_changes_nothing_unless_it_is_its_own_or_listed() -> None:
    async with client() as http:
        foreign_read = await http.get("/x", headers={"origin": FOREIGN})
        listed_read = await http.get("/x", headers={"origin": LISTED})
        foreign_write = await http.post("/x", headers={"origin": FOREIGN})
        own_write = await http.post("/x", headers={"origin": OWN})
        listed_write = await http.post("/x", headers={"origin": LISTED})
        cli_write = await http.post("/x")
    assert foreign_read.status_code == 200 and "access-control-allow-origin" not in foreign_read.headers
    assert (
        foreign_read.headers["referrer-policy"] == "no-referrer"
        and foreign_read.headers["x-content-type-options"] == "nosniff"
    )
    assert listed_read.headers["access-control-allow-origin"] == LISTED and listed_read.headers["vary"] == "Origin"
    assert foreign_write.status_code == 403 and foreign_write.json()["error"] == "cross-origin request refused"
    assert (own_write.status_code, listed_write.status_code, cli_write.status_code) == (200, 200, 200)
    assert listed_write.headers["access-control-allow-credentials"] == "true"


async def test_a_preflight_is_answered_for_a_listed_origin_and_refused_for_any_other() -> None:
    async with client() as http:
        listed = await http.options("/x", headers={"origin": LISTED, "access-control-request-method": "POST"})
        foreign = await http.options("/x", headers={"origin": FOREIGN, "access-control-request-method": "POST"})
        plain = await http.options("/x")
    assert listed.status_code == 204 and listed.headers["access-control-allow-methods"] == "GET, POST, PUT, DELETE"
    assert listed.headers["access-control-allow-headers"] == "Authorization, Content-Type"
    assert listed.headers["access-control-allow-origin"] == LISTED
    assert foreign.status_code == 403 and plain.status_code == 405


@pytest.mark.parametrize(
    ("headers", "let_in"),
    [
        ((("host", HOST), ("origin", OWN)), True),
        ((("host", HOST), ("origin", LISTED)), True),
        ((("host", HOST), ("origin", FOREIGN)), False),
        ((("host", HOST),), False),
        ((("host", "rebound.example:7466"), ("origin", OWN)), False),
    ],
)
async def test_a_socket_from_a_host_or_origin_that_is_not_allowed_is_refused_before_it_is_accepted(
    headers: tuple[tuple[str, str], ...], let_in: bool
) -> None:
    async with AsgiSocket(APP, "/ws", headers=headers) as ws:
        assert await ws.accepted() is let_in
        if not let_in:
            assert ws.closed == (CLOSE_FORBIDDEN, "refused")


async def test_anything_but_a_request_or_a_socket_passes_straight_through() -> None:
    seen: list[str] = []

    async def inner(scope: dict[str, Any], receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        return None

    await SecurityMiddleware(inner, lambda: RULES)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]
