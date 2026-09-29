# SPDX-License-Identifier: Apache-2.0
"""The daemon as a command sees it: its routes answered in memory, through the opener a `DaemonClient` is given.

`Daemon` proves it holds the admin token as the real daemon does, answers the routes the commands use, and keeps every
request it was sent; a test makes a route refuse, answer as it says, or the whole daemon be down.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sim_mirror.daemon import health


class Response:
    """What the daemon answered a request with, as `urllib` hands it over."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@dataclass
class Daemon:
    """The daemon's routes the commands use, in memory; down until `up`. It proves it holds the admin token that
    `admin` reads, as the real daemon does -- without that, it is something else on the port."""

    up: bool = True
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    refuse: set[str] = field(default_factory=set)
    devices: list[dict[str, Any]] = field(default_factory=list)
    #: The settings changes waiting to be confirmed.
    pending: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    admin: Callable[[], str] | None = None
    #: What a route answers, by method and path, for routes the fake does not answer on its own.
    answers: dict[tuple[str, str], Any] = field(default_factory=dict)

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        path = request.full_url.split("7466", 1)[1] if "7466" in request.full_url else request.full_url
        path, _, query = path.partition("?")
        body = json.loads(request.data) if request.data else None  # type: ignore[arg-type]
        with self.lock:
            self.requests.append((request.get_method(), path, body))
        if not self.up:
            raise urllib.error.URLError("connection refused")
        if path == "/healthz":
            nonce = urllib.parse.parse_qs(query).get("nonce", [""])[0]
            proof = health.proof(self.admin(), nonce) if self.admin is not None else "none"
            return Response(json.dumps({"ok": True, "data": {"port": 7466, "proof": proof}}).encode())
        if path in self.refuse:
            raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(b'{"detail": "refused here"}'))  # type: ignore[arg-type]
        return Response(json.dumps(self.answer(request.get_method(), path, body)).encode())

    def answer(self, method: str, path: str, body: Any) -> Any:
        if path == "/api/v1/agent/call":
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}
        data: Any = {"port": 7466}
        if (method, path) in self.answers:
            data = self.answers[(method, path)]
        elif path == "/api/v1/admin/tokens":
            data = {"id": "t1", "token": "agent-token"}
        elif path == "/api/v1/admin/login-codes":
            data = {"code": "c0de", "url": f"/viewer/{body['scope']}#code=c0de"}
        elif path == "/api/v1/admin/settings-confirmations":
            data = {"pending": self.pending}
        elif path.endswith("/devices"):
            data = {"devices": self.devices}
        elif path.endswith("/device"):
            data = {"udid": body["udid"]}
        return {"ok": True, "data": data}

    def made(self, method: str, path: str) -> list[Any]:
        return [body for seen, where, body in self.requests if (seen, where) == (method, path)]
