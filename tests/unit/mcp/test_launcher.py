# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: the daemon started when it is down, an agent token minted for the project and revoked after, the
relay in-process, and a lease held while the client runs."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.daemon import health
from sim_mirror.daemon.lifecycle import START_LOCK_FILE, clear_stopped, mark_stopped
from sim_mirror.mcp import launcher
from sim_mirror.mcp.launcher import (
    STOPPED_ON_PURPOSE,
    DaemonClient,
    DaemonUnavailable,
    LeaseKeeper,
    LeaseRefused,
    ensure_daemon,
    older,
    reviver,
    run,
)
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
    """The daemon's routes the launcher uses, answered in memory; down until `up` is set. An `impostor` answers on the
    port without being able to prove it holds the admin token."""

    def __init__(self, *, up: bool = True, impostor: bool = False, server: str | None = None) -> None:
        self.up = up
        self.impostor = impostor
        #: What the daemon calls itself, as ``sim-mirror/<version>``; this version's when not set.
        self.server = server or f"sim-mirror/{__version__}"
        self.requests: list[tuple[str, str, str | None, Any]] = []
        self.refuse_tokens: bytes | None = None
        #: The status the lease route refuses with, when it does.
        self.refuse_lease: int | None = None
        self.lock = threading.Lock()

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        path, _, query = request.full_url.removeprefix(URL).partition("?")
        body = json.loads(request.data) if request.data else None  # type: ignore[arg-type]
        with self.lock:
            self.requests.append((request.get_method(), path, request.get_header("Authorization"), body))
        if not self.up:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        if path == "/healthz":
            nonce = urllib.parse.parse_qs(query).get("nonce", [""])[0]
            proof = "forged" if self.impostor else health.proof(ADMIN, nonce)
            data = {"port": 7466, "proof": proof, "server": self.server}
            return Response(json.dumps({"ok": True, "data": data}).encode())
        if path == "/api/v1/admin/tokens" and self.refuse_tokens is not None:
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(self.refuse_tokens))  # type: ignore[arg-type]
        if path == "/api/v1/agent/lease" and self.refuse_lease is not None:
            raise urllib.error.HTTPError(request.full_url, self.refuse_lease, "No", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        answers: dict[tuple[str, str], Any] = {
            ("POST", "/api/v1/admin/tokens"): {"ok": True, "data": {"id": "t1", "token": "agent-token"}},
            ("DELETE", "/api/v1/admin/tokens/t1"): {"ok": True, "data": {"revoked": True}},
            ("POST", "/api/v1/agent/lease"): {"ok": True, "data": {"scope": "tp-1", "agent_idle_s": 600}},
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
    assert daemon.made("GET", "/healthz") == [(None, None)]
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


def test_a_listener_that_is_not_the_daemon_is_sent_no_credential_and_is_not_started_over() -> None:
    squatter = FakeDaemon(impostor=True)
    client = DaemonClient(URL, ADMIN, opener=squatter)
    assert client.probe() == launcher.OTHER and not client.healthy()
    with pytest.raises(DaemonUnavailable, match="not your SimMirror daemon, so nothing was sent to it"):
        client.post("/api/v1/admin/tokens", {})
    assert client.renew_lease("agent-token", "tp-1") is False
    client.revoke("t1")
    started: list[str] = []
    with pytest.raises(DaemonUnavailable, match=r"server\.port"):
        ensure_daemon(client, lambda: started.append("serve --detach"))
    assert started == []
    assert {(path, auth) for _, path, auth, _ in squatter.requests} == {("/healthz", None)}

    late = FakeDaemon(up=False)

    def squat() -> None:
        late.up, late.impostor = True, True

    with pytest.raises(DaemonUnavailable, match="not your SimMirror daemon"):
        ensure_daemon(DaemonClient(URL, ADMIN, opener=late), squat, clock=ManualClock(), sleep=lambda s: None)
    assert {auth for _, _, auth, _ in late.requests} == {None}


def test_a_listener_that_answers_with_an_error_or_garbage_is_not_the_daemon() -> None:
    def refusing(request: urllib.request.Request, timeout: float) -> Response:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]

    def garbage(request: urllib.request.Request, timeout: float) -> Response:
        return Response(b"<html>hello</html>")

    def unproven(request: urllib.request.Request, timeout: float) -> Response:
        return Response(b'{"ok": true, "data": ["no", "proof"]}')

    for opener in (refusing, garbage, unproven):
        assert DaemonClient(URL, ADMIN, opener=opener).probe() == launcher.OTHER


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


def test_a_daemon_of_an_older_simmirror_is_stopped_and_this_version_started_in_its_place() -> None:
    daemon = FakeDaemon(server="sim-mirror/1.2.0")
    client = DaemonClient(URL, ADMIN, opener=daemon)
    clock = ManualClock()
    retired: list[str] = []
    started: list[str] = []

    def retire() -> None:
        retired.append(str(client.server))
        daemon.up = False

    def start() -> None:
        started.append("serve")
        daemon.up, daemon.server = True, f"sim-mirror/{__version__}"

    ensure_daemon(client, start, retire=retire, clock=clock, sleep=clock.advance)
    assert retired == ["sim-mirror/1.2.0"] and started == ["serve"] and client.server == f"sim-mirror/{__version__}"
    ensure_daemon(client, start, retire=retire, clock=clock, sleep=clock.advance)
    assert started == ["serve"], "this version's daemon is used as it is"


def test_a_newer_or_unnamed_daemon_is_used_as_it_is_and_one_that_will_not_stop_is_said() -> None:
    for server in ("sim-mirror/99.0.0", "something-else", "sim-mirror/x.y"):
        daemon = FakeDaemon(server=server)
        ensure_daemon(DaemonClient(URL, ADMIN, opener=daemon), lambda: pytest.fail("started"), retire=pytest.fail)
    stubborn = DaemonClient(URL, ADMIN, opener=FakeDaemon(server="sim-mirror/0.2.0"))
    clock = ManualClock()
    with pytest.raises(DaemonUnavailable, match=r"older SimMirror daemon \(sim-mirror/0\.2\.0\) did not stop"):
        ensure_daemon(stubborn, lambda: None, retire=lambda: None, clock=clock, sleep=clock.advance)
    ensure_daemon(stubborn, lambda: pytest.fail("started"))  # without retire, as `sim-mirror open` does
    assert older("sim-mirror/1.2.0", "2.0.0") and not older("sim-mirror/2.0.0", "2.0.0")
    assert not older(None) and older("sim-mirror/2.0.0+local", "2.0.1")


class LeaseClient:
    """The daemon's lease route as a `LeaseKeeper` asks it: answering `answer`, refusing, or not reached (None)."""

    def __init__(self, answer: dict[str, Any] | None = None, *, refused: bool = False) -> None:
        self.answer = answer
        self.refused = refused
        self.leased: list[str] = []
        self.asked = threading.Event()

    def lease(self, token: str) -> dict[str, Any] | None:
        self.leased.append(token)
        self.asked.set()
        if self.refused:
            raise LeaseRefused("authentication required")
        return self.answer


def test_a_lease_is_taken_at_the_first_call_renewed_while_calls_come_and_left_to_lapse_once_they_stop() -> None:
    clock = ManualClock()
    client = LeaseClient({"scope": "tp-1", "agent_idle_s": 600})
    keeper = LeaseKeeper(client, "agent-token", every_s=30, clock=clock)  # type: ignore[arg-type]
    assert keeper.wait_s() is None, "no call yet: nothing is held"
    clock.advance(3600)
    assert keeper.wait_s() is None, "however long the client sits there"
    keeper.called()
    assert keeper.wait_s() == 0, "taken at once"
    assert keeper.renew() and keeper.idle_s == 600 and client.leased == ["agent-token"]
    assert keeper.wait_s() == 30
    clock.advance(30)
    assert keeper.wait_s() == 0
    keeper.renew()
    clock.advance(570)
    assert keeper.wait_s() is None, "ten quiet minutes, as the scope's setting says: let lapse"
    keeper.called()
    assert keeper.wait_s() == 0, "the next call takes it again at once"
    client.answer = None
    assert keeper.renew() and keeper.idle_s == 600, "a daemon not reached is asked again next time"
    assert keeper.wait_s() == 30
    for odd in (True, -5, "600", 0):
        client.answer = {"agent_idle_s": odd}
        keeper.renew()
    assert keeper.idle_s == 600
    client.refused = True
    assert keeper.renew() is False


def test_the_lease_thread_waits_for_a_call_renews_until_stopped_and_ends_on_a_refused_token() -> None:
    client = LeaseClient({"scope": "tp-1"})
    keeper = LeaseKeeper(client, "agent-token", every_s=0.001)  # type: ignore[arg-type]
    thread = threading.Thread(target=keeper.run, daemon=True)
    thread.start()
    assert not client.asked.wait(0.05) and client.leased == [], "nothing is renewed before the first call"
    keeper.called()
    assert client.asked.wait(5)
    keeper.stop()
    thread.join(5)
    assert not thread.is_alive() and client.leased
    refused = LeaseClient(refused=True)
    keeper = LeaseKeeper(refused, "agent-token", every_s=0.001)  # type: ignore[arg-type]
    keeper.called()
    thread = threading.Thread(target=keeper.run, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and refused.leased == ["agent-token"], "a revoked token is not renewed again"


@pytest.mark.parametrize(("status", "refused"), [(401, True), (403, True), (500, False)])
def test_a_lease_the_daemon_refuses_for_its_token_says_so_and_any_other_failure_is_tried_again(
    status: int, refused: bool
) -> None:
    daemon = FakeDaemon()
    client = DaemonClient(URL, ADMIN, opener=daemon)
    assert client.lease("agent-token") == {"scope": "tp-1", "agent_idle_s": 600}
    daemon.refuse_lease = status
    if refused:
        with pytest.raises(LeaseRefused):
            client.lease("agent-token")
    else:
        assert client.lease("agent-token") is None
    assert client.renew_lease("agent-token", "tp-1") is False


def test_mcp_serves_a_project_through_the_daemon_holding_its_lease_and_revoking_its_token(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}},
    ]
    stdout = io.StringIO()
    scope = Scope.named("tp-1")
    leased = threading.Event()

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        answer = daemon(request, timeout)
        if request.full_url.endswith("/agent/lease"):
            leased.set()
        return answer

    client = DaemonClient(URL, ADMIN, opener=opener)

    class McpClient:
        """An MCP client that says what it has to say, and stays until the lease its call took is held."""

        def __iter__(self) -> Iterator[str]:
            for message in messages:
                yield json.dumps(message) + "\n"
            assert leased.wait(5)

    answered = run(
        scope,
        [tmp_path],
        client=client,
        start_daemon=lambda: pytest.fail("the daemon was up"),
        env={"HOME": str(tmp_path)},
        stdin=McpClient(),  # type: ignore[arg-type]
        stdout=stdout,
        opener=opener,
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
    asked = [path for _, path, _, _ in daemon.requests]
    assert asked.index("/api/v1/agent/call") < asked.index("/api/v1/agent/lease"), "leased from the first call"
    assert daemon.made("DELETE", "/api/v1/admin/tokens/t1") == [("Bearer admin-token", None)]
    assert launcher.relay_argv()[:2] == ["--url-env", "SIM_MIRROR_URL"]
    assert launcher.relay_argv()[-4:] == ["--roots-header", "X-SimMirror-Roots", "--server-version", __version__]
    assert replies[0]["result"]["serverInfo"] == {"name": "sim-mirror", "version": __version__}, "says which it is"


def test_a_daemon_found_gone_is_started_again_unless_a_person_stopped_it_which_is_said_once_for_each_stop(
    tmp_path: Path,
) -> None:
    daemon = FakeDaemon(up=False)
    client = DaemonClient(URL, ADMIN, opener=daemon)
    started: list[bool] = []

    def start() -> None:
        started.append((tmp_path / START_LOCK_FILE).exists())
        daemon.up = True

    revive = reviver(client, start, tmp_path)
    assert revive() is None and started == [True], "started, under the run folder's start lock"
    daemon.up = False
    mark_stopped(tmp_path)
    assert revive() == STOPPED_ON_PURPOSE and started == [True], "a person stopped it: said, and not started"
    assert revive() is None and len(started) == 2, "asked again, it is started"
    daemon.up = False
    assert revive() is None and len(started) == 3, "this stop was said already"
    daemon.up = False
    mark_stopped(tmp_path)
    assert revive() == STOPPED_ON_PURPOSE and len(started) == 3, "a stop of its own is said again"
    clear_stopped(tmp_path)
    assert revive() is None and len(started) == 4
    assert reviver(client, pytest.fail)() is None, "the daemon is up: nothing is started"
    squatter = reviver(DaemonClient(URL, ADMIN, opener=FakeDaemon(impostor=True)), pytest.fail)
    said = squatter()
    assert said is not None and said.startswith("The SimMirror daemon is not running, and could not be started again:")
    assert "not your SimMirror daemon" in said


def test_mcp_brings_the_daemon_back_for_a_call_that_finds_it_gone_and_sends_the_call_again(tmp_path: Path) -> None:
    daemon = FakeDaemon(up=False)
    client = DaemonClient(URL, ADMIN, opener=daemon)
    starts: list[str] = []

    def start() -> None:
        starts.append("serve")
        daemon.up = True

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        # The person stops the daemon between the client's first call and its second.
        if request.full_url.endswith("/agent/call") and len(daemon.made("POST", "/api/v1/agent/call")) == 1:
            mark_stopped(tmp_path)
            daemon.up = False
        return daemon(request, timeout)

    call = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}}
    stdout = io.StringIO()
    answered = run(
        Scope.named("tp-1"),
        [tmp_path],
        client=client,
        start_daemon=start,
        stdin=io.StringIO("".join(json.dumps({**call, "id": n}) + "\n" for n in (1, 2, 3))),
        stdout=stdout,
        opener=opener,
        run_dir=tmp_path,
        env={},
    )
    assert answered == 0
    texts = [json.loads(line)["result"]["content"][0]["text"] for line in stdout.getvalue().splitlines()]
    assert texts == ["ok", STOPPED_ON_PURPOSE, "ok"]
    assert starts == ["serve", "serve"], "first launch, then the call after the stop was said"
    assert len(daemon.made("POST", "/api/v1/agent/call")) == 4, "ok; found gone; found gone, then sent again"
    assert len(daemon.made("POST", "/api/v1/admin/tokens")) == 1, "its token is still good"
