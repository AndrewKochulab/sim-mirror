# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: the daemon started when it is down, an agent token minted for the project and revoked after, the
relay in-process, and a lease held while the client runs."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.mcp import launcher
from sim_mirror.mcp.launcher import DaemonClient, DaemonUnavailable, ensure_daemon, keep_leased, run
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import ManualClock

URL = "http://127.0.0.1:7466"
ADMIN = "admin-token"


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeDaemon:
    """The daemon's routes the launcher uses, answered in memory; down until `up` is set."""

    def __init__(self, *, up: bool = True) -> None:
        self.up = up
        self.requests: list[tuple[str, str, str | None, Any]] = []
        self.refuse_tokens: bytes | None = None
        self.lock = threading.Lock()

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        path = request.full_url.removeprefix(URL)
        body = json.loads(request.data) if request.data else None  # type: ignore[arg-type]
        with self.lock:
            self.requests.append((request.get_method(), path, request.get_header("Authorization"), body))
        if not self.up:
            raise urllib.error.URLError("connection refused")
        if path == "/api/v1/admin/tokens" and self.refuse_tokens is not None:
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(self.refuse_tokens))  # type: ignore[arg-type]
        answers: dict[tuple[str, str], Any] = {
            ("GET", "/healthz"): {"ok": True, "data": {"port": 7466}},
            ("POST", "/api/v1/admin/tokens"): {"ok": True, "data": {"id": "t1", "token": "agent-token"}},
            ("DELETE", "/api/v1/admin/tokens/t1"): {"ok": True, "data": {"revoked": True}},
            ("POST", "/api/v1/agent/lease"): {"ok": True, "data": {"scope": "tp-1"}},
            ("GET", "/api/v1/agent/manifest"): {"tools": [{"name": "sim_device"}], "instructions": "look first"},
            ("POST", "/api/v1/agent/call"): {"content": [{"type": "text", "text": "ok"}], "isError": False},
        }
        return Response(json.dumps(answers.get((request.get_method(), path), [])).encode())

    def made(self, method: str, path: str) -> list[tuple[str | None, Any]]:
        return [(auth, body) for seen, where, auth, body in self.requests if (seen, where) == (method, path)]


def test_the_daemon_is_asked_with_the_admin_token_and_what_it_refuses_is_said() -> None:
    daemon = FakeDaemon()
    client = DaemonClient(URL + "/", ADMIN, opener=daemon)
    assert client.healthy() and client.url == URL
    assert client.mint_agent_token("tp-1", ["/Users/me/Notes"], "a label") == ("t1", "agent-token")
    assert daemon.made("POST", "/api/v1/admin/tokens") == [
        ("Bearer admin-token", {"kind": "agent", "scopes": ["tp-1"], "roots": ["/Users/me/Notes"], "label": "a label"})
    ]
    assert client.renew_lease("agent-token", "tp-1") is True
    assert daemon.made("POST", "/api/v1/agent/lease") == [("Bearer agent-token", {})]
    client.revoke("t1")
    daemon.refuse_tokens = b'{"detail": "this needs the admin token"}'
    with pytest.raises(DaemonUnavailable, match="the daemon refused /api/v1/admin/tokens: this needs the admin token"):
        client.mint_agent_token("tp-1", [], "x")
    with pytest.raises(DaemonUnavailable, match="answered /api/v1/admin/nowhere with something unexpected"):
        client.post("/api/v1/admin/nowhere", {})
    daemon.up = False
    assert not client.healthy() and client.renew_lease("agent-token", "tp-1") is False
    client.revoke("t1")
    with pytest.raises(DaemonUnavailable, match="could not be reached"):
        client.post("/api/v1/admin/tokens", {})


def test_a_daemon_that_is_down_is_started_and_waited_for_and_one_that_never_comes_up_is_said() -> None:
    daemon = FakeDaemon(up=False)
    client = DaemonClient(URL, ADMIN, opener=daemon)
    clock = ManualClock()
    started: list[str] = []

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        if clock.now >= 100.5:
            daemon.up = True

    ensure_daemon(client, lambda: started.append("serve --detach"), clock=clock, sleep=sleep)
    assert started == ["serve --detach"]
    ensure_daemon(client, lambda: started.append("again"), clock=clock, sleep=sleep)
    assert started == ["serve --detach"]
    daemon.up = False
    with pytest.raises(DaemonUnavailable, match=r"did not start within 10s; run `sim-mirror serve` to see why"):
        ensure_daemon(client, lambda: None, clock=clock, sleep=clock.advance)


def test_a_lease_is_renewed_at_once_and_then_until_it_is_stopped() -> None:
    renewed: list[tuple[str, str]] = []
    stop = threading.Event()

    class Client:
        def renew_lease(self, token: str, scope_id: str) -> bool:
            renewed.append((token, scope_id))
            if len(renewed) == 2:
                stop.set()
            return True

    keep_leased(Client(), "agent-token", "tp-1", stop, every_s=0.001)  # type: ignore[arg-type]
    assert renewed == [("agent-token", "tp-1")] * 2


def test_mcp_serves_a_project_through_the_daemon_holding_its_lease_and_revoking_its_token(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    client = DaemonClient(URL, ADMIN, opener=daemon)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}},
    ]
    stdout = io.StringIO()
    scope = Scope.named("tp-1")
    answered = run(
        scope,
        [tmp_path],
        client=client,
        start_daemon=lambda: pytest.fail("the daemon was up"),
        env={"HOME": str(tmp_path)},
        stdin=io.StringIO("".join(json.dumps(message) + "\n" for message in messages)),
        stdout=stdout,
        opener=daemon,
    )
    assert answered == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["result"]["instructions"] == "look first" and replies[1]["result"]["content"][0]["text"] == "ok"
    ((_, minted),) = daemon.made("POST", "/api/v1/admin/tokens")
    assert (
        minted["scopes"] == ["tp-1"]
        and minted["roots"] == [str(tmp_path)]
        and minted["label"].startswith("sim-mirror mcp")
    )
    assert [auth for auth, _ in daemon.made("GET", "/api/v1/agent/manifest")] == [None]
    assert daemon.made("POST", "/api/v1/agent/lease")[0][0] == "Bearer agent-token"
    assert daemon.made("DELETE", "/api/v1/admin/tokens/t1") == [("Bearer admin-token", None)]
    assert launcher.relay_argv()[:2] == ["--url-env", "SIM_MIRROR_URL"]
