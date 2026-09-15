# SPDX-License-Identifier: Apache-2.0
"""What keeps a local SimMirror server from being used by a web page it did not choose.

A server on 127.0.0.1 is still reachable from every page open in the person's browser: a page can post to it, frame
it, or point its own domain at 127.0.0.1 and read it as its own. So a SimMirror server

* answers only for the names it is served on -- the **Host allowlist**, against DNS rebinding;
* takes a state-changing request or a socket only from an **origin it was told of**, compared exactly (scheme, host and
  port) -- its own pages' origin and ``security.allowed_origins``. A request with no Origin is not from a page (a CLI
  with its token), and the token decides it;
* sends **CORS** headers only to the listed origins, never ``*``;
* tells the browser who may **frame** its pages (``frame-ancestors``: itself and ``security.frame_ancestors``), and
  sends no referrer, so a ticket or a code in a URL does not leave in one.

`SecurityMiddleware` is raw ASGI, so it sees WebSocket upgrades as well as requests: a socket from a Host or an Origin
that is not allowed is refused before it is accepted. The rules are read on every request, so a changed setting
applies at once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as AsgiScope

from sim_mirror.protocol import CLOSE_FORBIDDEN

LOOPBACK_NAMES = ("127.0.0.1", "localhost")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CORS_METHODS = "GET, POST, PUT, DELETE"
CORS_HEADERS = "Authorization, Content-Type"
CORS_MAX_AGE_S = 600


def origin_of(value: str | None) -> str | None:
    """An Origin -- or any URL -- as ``scheme://host[:port]``, lowercased, without a default port; None when it is not
    an http(s) origin (absent, ``null``, a file)."""
    if not value:
        return None
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + (f":{port}" if port is not None and port != default else "")


@dataclass(frozen=True)
class SiteRules:
    """Who a server on this port answers to."""

    port: int
    allowed_origins: tuple[str, ...] = ()
    frame_ancestors: tuple[str, ...] = ()

    @property
    def own_origins(self) -> frozenset[str]:
        return frozenset(f"http://{name}:{self.port}" for name in LOOPBACK_NAMES)

    @property
    def listed_origins(self) -> frozenset[str]:
        return frozenset(origin for origin in map(origin_of, self.allowed_origins) if origin)

    def host_allowed(self, host: str | None) -> bool:
        return (host or "").strip().lower() in {f"{name}:{self.port}" for name in LOOPBACK_NAMES}

    def origin_allowed(self, origin: str | None) -> bool:
        normalized = origin_of(origin)
        return normalized is not None and normalized in self.own_origins | self.listed_origins

    def cors_allowed(self, origin: str | None) -> bool:
        normalized = origin_of(origin)
        return normalized is not None and normalized in self.listed_origins

    def headers(self) -> list[tuple[bytes, bytes]]:
        ancestors = " ".join(["'self'", *(origin for origin in map(origin_of, self.frame_ancestors) if origin)])
        return [
            (b"content-security-policy", f"frame-ancestors {ancestors}".encode("latin-1")),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]


def _cors(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-allow-credentials", b"true"),
        (b"vary", b"Origin"),
    ]


def _with_headers(response: Response, extra: Iterable[tuple[bytes, bytes]]) -> Response:
    response.raw_headers.extend(extra)
    return response


class SecurityMiddleware:
    """The Host allowlist, the Origin check, CORS for listed origins and the security headers, for requests and
    sockets alike."""

    def __init__(self, app: ASGIApp, rules: Callable[[], SiteRules]) -> None:
        self.app = app
        self._rules = rules

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        rules = self._rules()
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if kind == "websocket":
            if rules.host_allowed(headers.get("host")) and rules.origin_allowed(origin):
                await self.app(scope, receive, send)
                return
            await receive()
            await send({"type": "websocket.close", "code": CLOSE_FORBIDDEN, "reason": "refused"})
            return
        refusal = self._refusal(rules, scope["method"], headers.get("host"), origin)
        if refusal is not None:
            await _with_headers(refusal, rules.headers())(scope, receive, send)
            return
        cors = _cors(origin_of(origin) or "") if rules.cors_allowed(origin) else []
        if scope["method"] == "OPTIONS" and cors:
            preflight = [
                (b"access-control-allow-methods", CORS_METHODS.encode("latin-1")),
                (b"access-control-allow-headers", CORS_HEADERS.encode("latin-1")),
                (b"access-control-max-age", str(CORS_MAX_AGE_S).encode("latin-1")),
            ]
            await _with_headers(Response(status_code=204), [*rules.headers(), *cors, *preflight])(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *rules.headers(), *cors]}
            await send(message)

        await self.app(scope, receive, with_headers)

    @staticmethod
    def _refusal(rules: SiteRules, method: str, host: str | None, origin: str | None) -> Response | None:
        if not rules.host_allowed(host):
            return JSONResponse({"ok": False, "error": "this server does not answer for that host"}, status_code=400)
        if origin is not None and method == "OPTIONS" and not rules.cors_allowed(origin):
            return JSONResponse({"ok": False, "error": "cross-origin request refused"}, status_code=403)
        if origin is not None and method not in SAFE_METHODS and not rules.origin_allowed(origin):
            return JSONResponse({"ok": False, "error": "cross-origin request refused"}, status_code=403)
        return None
