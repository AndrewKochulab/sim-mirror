# SPDX-License-Identifier: Apache-2.0
"""A relay after its server went and came back: on the port it had, on another one, or not yet -- under ``sim-mirror
mcp``, which proves what answers before its token goes there, and run by a host itself, which reads the daemon's run
folder."""

from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from sim_mirror._version import __version__
from sim_mirror.daemon import health, lifecycle
from sim_mirror.daemon.lifecycle import START_LOCK_FILE, DaemonInfo, write_info
from sim_mirror.mcp import launcher, relay
from sim_mirror.mcp.launcher import DaemonClient, ensure_daemon, follower, reviver, run
from sim_mirror.scope import Scope
from sim_mirror.storage.private import ensure_private_dir, file_lock

ADMIN = "admin-token"
AGENT = "agent-token"
FLAGS = ["--header", "X-SimMirror-Token=SIM_MIRROR_TOKEN", "--header", "X-SimMirror-Scope=SIM_MIRROR_SCOPE"]
ENV = {
    "SIM_MIRROR_URL": "http://127.0.0.1:7466/api/v1/agent",
    "SIM_MIRROR_TOKEN": AGENT,
    "SIM_MIRROR_SCOPE": "tp-1",
}
MANIFEST = {"tools": [{"name": "sim_device"}], "instructions": "look first"}


class Response:
    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class Mac:
    """This Mac's loopback ports, and what listens on each: this user's daemon (`OURS`), something that cannot prove
    it is (`OTHER`), or nothing. Every request is written down with the credential it carried."""

    def __init__(self, **ports: str) -> None:
        self.ports = {int(port.removeprefix("p")): kind for port, kind in ports.items()}
        self.requests: list[tuple[int, str, str, str | None]] = []
        self.lock = threading.Lock()

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        parts = urllib.parse.urlsplit(request.full_url)
        assert parts.hostname == "127.0.0.1" and parts.port is not None
        credential = request.get_header("Authorization") or request.get_header("X-simmirror-token")
        with self.lock:
            self.requests.append((parts.port, request.get_method(), parts.path, credential))
            kind = self.ports.get(parts.port)
        if kind is None:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        if parts.path == "/healthz":
            nonce = urllib.parse.parse_qs(parts.query)["nonce"][0]
            proof = health.proof(ADMIN, nonce) if kind == launcher.OURS else "forged"
            return Response({"ok": True, "data": {"proof": proof, "server": f"sim-mirror/{__version__}"}})
        answers: dict[tuple[str, str], Any] = {
            ("POST", "/api/v1/admin/tokens"): {"ok": True, "data": {"id": "t1", "token": AGENT}},
            ("DELETE", "/api/v1/admin/tokens/t1"): {"ok": True, "data": {"revoked": True}},
            ("POST", "/api/v1/agent/lease"): {"ok": True, "data": {}},
            ("GET", "/api/v1/agent/manifest"): MANIFEST,
            ("POST", "/api/v1/agent/call"): {"content": [{"type": "text", "text": f"ok from {parts.port}"}]},
        }
        return Response(answers[(request.get_method(), parts.path)])

    def credentials(self) -> set[tuple[int, str]]:
        """Each port a credential was sent to, and the path it went with."""
        return {(port, path) for port, _, path, credential in self.requests if credential}


def private_run(tmp_path: Path) -> Path:
    return ensure_private_dir(tmp_path / "run")


def texts(stdout: io.StringIO) -> list[str]:
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return [reply["result"]["content"][0]["text"] for reply in replies if "content" in reply.get("result", {})]


def call(n: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}}


def lines(*messages: dict[str, Any]) -> io.StringIO:
    return io.StringIO("".join(json.dumps(message) + "\n" for message in messages))


# -- a relay a host runs itself ----------------------------------------------------------------------------------------


def test_a_relay_given_the_run_folder_follows_the_daemon_to_the_port_it_listens_on_now(tmp_path: Path) -> None:
    run_dir = private_run(tmp_path)
    write_info(run_dir, DaemonInfo(os.getpid(), 7467, __version__))
    mac = Mac(p7467=launcher.OURS)

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        if request.full_url.endswith("/call") and any(path.endswith("/call") for _, _, path, _ in mac.requests):
            (run_dir / lifecycle.INFO_FILE).unlink()  # the daemon goes, after the first call
            mac.ports.clear()
        return mac(request, timeout)

    stdout = io.StringIO()
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    flags = [*FLAGS, "--run-dir", str(run_dir)]
    assert relay.main(flags, env=ENV, stdin=lines(listing, call(2), call(3)), stdout=stdout, opener=opener) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["result"] == {"tools": MANIFEST["tools"]}, "the list followed it from 7466 to 7467"
    assert texts(stdout) == [
        "ok from 7467",
        "Nothing listens at 127.0.0.1:7467 any more, so this call was not made. Start the server again -- "
        "`sim-mirror serve --detach` for SimMirror's daemon -- and call again, or reconnect this MCP server.",
    ]
    assert [(port, path.rsplit("/", 1)[-1]) for port, _, path, _ in mac.requests] == [
        (7466, "manifest"),
        (7467, "manifest"),
        (7467, "call"),
        (7467, "manifest"),  # whether the tools changed with the call
        (7467, "call"),
        (7467, "manifest"),
    ]


def test_a_relay_that_cannot_bring_its_server_back_says_in_one_line_how_to() -> None:
    stdout = io.StringIO()
    assert relay.main(FLAGS, env=ENV, stdin=lines(call(1)), stdout=stdout, opener=Mac()) == 0
    (said,) = texts(stdout)
    assert said.startswith("Nothing listens at 127.0.0.1:7466 any more") and "sim-mirror serve --detach" in said
    assert "\n" not in said and AGENT not in said


def test_where_the_server_listens_now_is_followed_only_to_another_loopback_address() -> None:
    mac = Mac(p7468=launcher.OURS)
    found = iter(["http://example.com:7468/api/v1/agent", "http://127.0.0.1:7466/api/v1/agent/", None])
    stdout = io.StringIO()
    messages = lines(call(1), call(2), call(3), call(4))
    relay.main(FLAGS, env=ENV, stdin=messages, stdout=stdout, opener=mac, locate=lambda: next(found, None))
    assert all(said.startswith("Nothing listens at 127.0.0.1:7466") for said in texts(stdout)[:3])
    assert {port for port, *_ in mac.requests} == {7466}, "nothing went off this Mac, nor anywhere else"

    stdout = io.StringIO()
    moved_on = "http://127.0.0.1:7468/api/v1/agent"
    relay.main(FLAGS, env=ENV, stdin=lines(call(1)), stdout=stdout, opener=mac, locate=lambda: moved_on)
    assert texts(stdout) == ["ok from 7468"], "a call nothing listened for is sent where the server listens now"

    moved = iter(["http://127.0.0.1:7467/api/v1/agent", "http://127.0.0.1:7468/api/v1/agent"])
    nobody = Mac()
    upstream = relay.Upstream(ENV["SIM_MIRROR_URL"], {}, nobody, locate=lambda: next(moved, None))
    assert relay.Relay(upstream).manifest()["instructions"].startswith("The SimMirror server could not be reached")
    assert upstream.base == "http://127.0.0.1:7467/api/v1/agent" and len(nobody.requests) == 2, "followed once"


def test_a_relay_that_brings_its_server_back_follows_it_to_where_it_came_back() -> None:
    mac = Mac()
    where: list[str | None] = [None]

    def revive() -> str | None:
        mac.ports[7469] = launcher.OURS
        where[0] = "http://127.0.0.1:7469/api/v1/agent"
        return None

    stdout = io.StringIO()
    relay.main(FLAGS, env=ENV, stdin=lines(call(1)), stdout=stdout, opener=mac, revive=revive, locate=lambda: where[0])
    assert texts(stdout) == ["ok from 7469"]


def test_the_run_folder_is_read_as_the_daemon_writes_it_and_believed_only_when_it_is_this_users_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (relay.DAEMON_FILE, relay.START_LOCK_FILE) == (lifecycle.INFO_FILE, lifecycle.START_LOCK_FILE)
    run_dir = private_run(tmp_path)
    info = run_dir / lifecycle.INFO_FILE
    assert relay.daemon_port(str(run_dir)) is None, "no daemon has written one"
    write_info(run_dir, DaemonInfo(os.getpid(), 7470, __version__))
    assert relay.daemon_port(str(run_dir)) == 7470
    assert relay.daemon_locate(str(run_dir), "http://localhost:7466/api/v1/agent/")() == (
        "http://127.0.0.1:7470/api/v1/agent"
    )
    assert relay.daemon_port(str(tmp_path / "nowhere")) is None

    info.chmod(0o620)
    assert relay.daemon_port(str(run_dir)) is None, "a file someone else may write to"
    info.chmod(0o600)
    run_dir.chmod(0o770)
    assert relay.daemon_port(str(run_dir)) is None, "a folder someone else may write to"
    run_dir.chmod(0o700)
    with monkeypatch.context() as patched:
        patched.setattr(relay.os, "getuid", lambda: os.geteuid() + 1)
        assert relay.daemon_port(str(run_dir)) is None, "a folder of another user's"

    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(info.read_text())
    info.unlink()
    info.symlink_to(elsewhere)
    assert relay.daemon_port(str(run_dir)) is None, "a link to a file elsewhere"
    info.unlink()
    info.mkdir()
    assert relay.daemon_port(str(run_dir)) is None, "not a file"
    info.rmdir()

    for written in (
        "not json",
        "[7470]",
        {"pid": True, "port": 7470},
        {"pid": os.getpid(), "port": 70000},
        {"pid": os.getpid(), "port": "7470"},
        {"pid": -1, "port": 7470},
        {"pid": 99_999_999, "port": 7470},
        {"pid": 1, "port": 7470},
    ):
        info.write_text(written if isinstance(written, str) else json.dumps(written))
        info.chmod(0o600)
        assert relay.daemon_port(str(run_dir)) is None, written


def test_a_relay_reading_the_run_folder_waits_for_a_daemon_being_started_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = private_run(tmp_path)
    read: list[int | None] = []
    reader = threading.Thread(target=lambda: read.append(relay.daemon_port(str(run_dir))))
    with file_lock(run_dir / START_LOCK_FILE):
        reader.start()
        time.sleep(0.2)
        assert read == [], "it waits while the start lock is held"
        write_info(run_dir, DaemonInfo(os.getpid(), 7471, __version__))
    reader.join(timeout=5)
    assert read == [7471], "and reads the port the daemon started on"

    def unlockable(descriptor: int, operation: int) -> None:
        raise OSError("no locks here")

    monkeypatch.setattr(relay.fcntl, "flock", unlockable)
    assert relay.daemon_port(str(run_dir)) == 7471, "a lock that cannot be taken is not waited for"


def test_a_host_gives_its_agents_relay_the_run_folder_on_its_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = relay.relay_command(run_dir=tmp_path / "run" / ".." / "run")
    assert argv[-2:] == ["--run-dir", str(tmp_path / "run")]
    assert "--run-dir" not in relay.relay_command()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert relay.relay_command(run_dir="~/run")[-1] == str(tmp_path / "run")
    write_info(private_run(tmp_path), DaemonInfo(os.getpid(), 7478, __version__))
    assert relay.daemon_port("~/run") == 7478, "as a client's configuration writes it, unexpanded"


# -- sim-mirror mcp ----------------------------------------------------------------------------------------------------


def test_a_daemon_started_again_on_another_port_is_found_there_and_proves_itself_before_any_credential_goes(
    tmp_path: Path,
) -> None:
    mac = Mac()
    client = DaemonClient("http://127.0.0.1:7466", ADMIN, opener=mac)
    config = ["http://127.0.0.1:7472"]  # server.port was changed while the daemon was down

    def start() -> None:
        mac.ports[7472] = launcher.OURS

    revive = reviver(client, start, tmp_path, lambda: config[0])
    assert revive() is None and client.url == "http://127.0.0.1:7472"
    assert follower(client, lambda: config[0])() == "http://127.0.0.1:7472/api/v1/agent"
    assert client.renew_lease(AGENT, "tp-1") is True
    assert mac.credentials() == {(7472, "/api/v1/agent/lease")}

    mac.ports[7473] = launcher.OTHER
    config[0] = "http://127.0.0.1:7473"
    assert follower(client, lambda: config[0])() is None, "what answers there is not this user's daemon"
    assert client.renew_lease(AGENT, "tp-1") is False
    assert mac.credentials() == {(7472, "/api/v1/agent/lease")}, "and was sent nothing"


def test_a_credential_goes_only_to_the_address_that_proved_itself_even_after_the_client_moves() -> None:
    mac = Mac(p7466=launcher.OURS, p7474=launcher.OURS)
    client = DaemonClient("http://127.0.0.1:7466", ADMIN, opener=mac)
    assert client.renew_lease(AGENT, "tp-1") is True
    client.move("http://127.0.0.1:7474/")
    assert client.renew_lease(AGENT, "tp-1") is True
    asked = [(port, path.rsplit("/", 1)[-1]) for port, _, path, _ in mac.requests]
    assert asked == [(7466, "healthz"), (7466, "lease"), (7474, "healthz"), (7474, "lease")]


def test_two_relays_bringing_the_daemon_back_at_once_start_one_and_both_find_it_on_its_new_port(tmp_path: Path) -> None:
    mac = Mac()
    config = "http://127.0.0.1:7475"
    starts: list[int] = []

    def start() -> None:
        starts.append(1)
        time.sleep(0.2)  # the other relay looks meanwhile, and waits for the start lock
        mac.ports[7475] = launcher.OURS

    clients = [DaemonClient("http://127.0.0.1:7466", ADMIN, opener=mac) for _ in range(2)]
    said: list[str | None] = []
    relays = [
        threading.Thread(target=lambda c=client: said.append(reviver(c, start, tmp_path, lambda: config)()))
        for client in clients
    ]
    for each in relays:
        each.start()
    for each in relays:
        each.join(timeout=10)
    assert said == [None, None] and starts == [1], "one daemon started"
    assert [client.url for client in clients] == [config, config]


def test_ensure_daemon_looks_where_the_daemon_listens_now_each_time() -> None:
    mac = Mac(p7476=launcher.OURS)
    client = DaemonClient("http://127.0.0.1:7466", ADMIN, opener=mac)
    ensure_daemon(client, lambda: pytest.fail("it was running"), locate=lambda: "http://127.0.0.1:7476")
    assert client.url == "http://127.0.0.1:7476"


def test_mcp_follows_a_daemon_that_came_back_on_another_port_and_its_lease_and_revoke_go_there(tmp_path: Path) -> None:
    mac = Mac(p7466=launcher.OURS)
    client = DaemonClient("http://127.0.0.1:7466", ADMIN, opener=mac)
    where = ["http://127.0.0.1:7466"]
    starts: list[str] = []

    def start() -> None:
        starts.append(where[0])
        mac.ports[7477] = launcher.OURS

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        # The daemon goes after the first call -- it crashed -- and its config now names another port.
        called = any(path.endswith("/call") for _, _, path, _ in mac.requests)
        if request.full_url.endswith("/agent/call") and called and 7466 in mac.ports:
            del mac.ports[7466]
            where[0] = "http://127.0.0.1:7477"
        return mac(request, timeout)

    stdout = io.StringIO()
    answered = run(
        Scope.named("tp-1"),
        [tmp_path],
        client=client,
        start_daemon=start,
        stdin=lines(call(1), call(2), call(3)),
        stdout=stdout,
        opener=opener,
        run_dir=tmp_path,
        env={},
        locate_daemon=lambda: where[0],
    )
    assert answered == 0
    assert texts(stdout) == ["ok from 7466", "ok from 7477", "ok from 7477"]
    assert starts == ["http://127.0.0.1:7477"], "started once, found on its new port"
    assert (7477, "/api/v1/admin/tokens/t1") in mac.credentials(), "its token revoked where the daemon is now"
    proved = [index for index, (port, _, path, _) in enumerate(mac.requests) if (port, path) == (7477, "/healthz")]
    sent = [index for index, (port, _, _, credential) in enumerate(mac.requests) if port == 7477 and credential]
    assert proved and min(proved) < min(sent), "the new port proved itself before the agent token went there"
