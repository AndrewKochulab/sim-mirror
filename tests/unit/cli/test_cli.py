# SPDX-License-Identifier: Apache-2.0
"""The ``sim-mirror`` command line, with no daemon, process, server, browser or Xcode: every command against a daemon
answered in memory, and a refusal a person can act on as one line and exit status 1."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

import sim_mirror.__main__ as module_entry
from sim_mirror import api
from sim_mirror._version import __version__
from sim_mirror.cli import context as context_module
from sim_mirror.cli.context import CliContext, serve_with_uvicorn
from sim_mirror.cli.main import main
from sim_mirror.cli.version import connector_names
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.core.runtime import Runtime
from sim_mirror.daemon import health
from sim_mirror.daemon.lifecycle import DaemonInfo, info_path, read_info, write_info
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.report import CheckResult, Report
from sim_mirror.protocol import PROTOCOL_VERSION
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import FakeConnector, FakeProcess


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@dataclass
class Daemon:
    """The daemon's routes the commands use, in memory; down until `up`. It proves it holds the admin token that
    `admin` reads, as the real daemon does -- without that, it is something else on the port."""

    up: bool = True
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    refuse: set[str] = field(default_factory=set)
    devices: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    admin: Callable[[], str] | None = None

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
        if path == "/api/v1/agent/manifest":
            return {"tools": [{"name": "sim_device"}], "instructions": "look first"}
        if path == "/api/v1/agent/call":
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}
        data: Any = {"port": 7466}
        if path == "/api/v1/admin/tokens":
            data = {"id": "t1", "token": "agent-token"}
        elif path == "/api/v1/admin/login-codes":
            data = {"code": "c0de", "url": f"/viewer/{body['scope']}#code=c0de"}
        elif path.endswith("/devices"):
            data = {"devices": self.devices}
        elif path.endswith("/device"):
            data = {"udid": body["udid"]}
        return {"ok": True, "data": data}

    def made(self, method: str, path: str) -> list[Any]:
        return [body for seen, where, body in self.requests if (seen, where) == (method, path)]


@dataclass
class Terminal:
    root: Path
    daemon: Daemon = field(default_factory=Daemon)
    stdin: str = ""
    spawned: list[tuple[tuple[str, ...], Path]] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    browser: bool = True
    served: list[tuple[FastAPI, str, int, bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cwd = self.root / "Notes"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.env = {
            "SIM_MIRROR_STATE_DIR": str(self.root / "state"),
            "SIM_MIRROR_RUN_DIR": str(self.root / "run"),
            "SIM_MIRROR_LOG_DIR": str(self.root / "logs"),
            "SIM_MIRROR_CLAIMS_DIR": str(self.root / "claims"),
            "SIM_MIRROR_CONFIG": str(self.root / "config.toml"),
        }
        self.out, self.err = io.StringIO(), io.StringIO()
        self.ctx = CliContext(
            env=self.env,
            cwd=self.cwd,
            stdout=self.out,
            stderr=self.err,
            stdin=io.StringIO(self.stdin),
            home=self.root,
            opener=self.daemon,
            open_url=self.open_url,
            spawn=self.spawn,
            serve=self.serve,
            python="/usr/bin/python3",
        )
        self.daemon.admin = lambda: self.ctx.tokens().admin_token()

    def open_url(self, url: str) -> bool:
        self.opened.append(url)
        return self.browser

    async def spawn(self, argv: Sequence[str], log_path: Path) -> FakeProcess:
        self.spawned.append((tuple(argv), log_path))
        self.daemon.up = True
        return FakeProcess(pid=5151)

    async def serve(self, app: FastAPI, host: str, port: int) -> None:
        self.served.append((app, host, port, info_path(self.root / "run").exists()))

    def __call__(self, *argv: str) -> int:
        return main(list(argv), ctx=self.ctx)

    def said(self) -> list[str]:
        return self.out.getvalue().splitlines()


# -- the command itself ----------------------------------------------------------------------------------------------


def test_without_a_command_it_shows_its_help_and_it_knows_its_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    here = Terminal(tmp_path)
    assert here() == 0 and "COMMAND" in here.out.getvalue()
    with pytest.raises(SystemExit) as exited:
        here("--version")
    assert exited.value.code == 0 and capsys.readouterr().out.strip() == f"sim-mirror {__version__}"
    assert here("version") == 0
    assert here.said()[-3:] == [f"sim-mirror {__version__}", f"protocol v{PROTOCOL_VERSION}", "connectors: idb, simctl"]
    extra = connector_names(entry_points=lambda group: [SimpleNamespace(name="android")])
    assert extra == ["android", "idb", "simctl"]


def test_the_process_context_and_the_module_entry_point_are_the_real_ones() -> None:
    real = CliContext.from_process()
    assert real.stdout is not None and real.cwd == Path.cwd() and dict(real.env) == dict(os.environ)
    assert module_entry.main is main
    assert set(api.__all__) <= set(dir(api)) and api.Runtime is Runtime


async def test_the_daemon_is_served_by_uvicorn_with_sans_io_websockets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ours = logging.getLogger("sim_mirror")
    monkeypatch.setattr(ours, "handlers", list(ours.handlers))
    monkeypatch.setattr(ours, "level", ours.level)
    seen: dict[str, Any] = {}

    class Server:
        def __init__(self, config: Any) -> None:
            seen["config"] = config

        async def serve(self) -> None:
            seen["served"] = True

    monkeypatch.setattr(context_module.uvicorn, "Server", Server)
    await serve_with_uvicorn(FastAPI(), "127.0.0.1", 7481)
    config = seen["config"]
    assert (config.host, config.port, config.ws, seen["served"]) == ("127.0.0.1", 7481, "websockets-sansio", True)
    # A stop is not held up by a viewer that takes no data: the runtime still closes, a few seconds later.
    grace = context_module.SHUTDOWN_GRACE_S
    assert config.timeout_graceful_shutdown == grace and 0 < grace <= 10
    # SimMirror's own lines go where uvicorn's do -- stderr, which a detached daemon appends to its log.
    logging.getLogger("sim_mirror.core.screen_relay").info("a viewer of U1 took nothing for 10s; letting it go")
    logging.getLogger("sim_mirror.core.screen_relay").debug("too fine to keep")
    logged = capsys.readouterr().err
    assert "a viewer of U1 took nothing for 10s; letting it go" in logged and "too fine" not in logged


def test_tools_lists_what_an_agent_is_offered_and_prints_the_manifest_as_json(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here("tools") == 0
    assert here.said()[0] == "sim_device: Your iOS Simulator."
    assert all(not line.startswith("sim_build_run") for line in here.said())
    here.out.truncate(0)
    here.out.seek(0)
    assert here("tools", "--json") == 0
    assert [tool["name"] for tool in json.loads(here.out.getvalue())["tools"]][:2] == ["sim_device", "sim_snapshot"]


# -- serve -----------------------------------------------------------------------------------------------------------


def test_serve_runs_the_daemon_here_and_says_where_while_its_file_names_it(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    (tmp_path / "config.toml").write_text("[stream]\nfps = 999\n")
    assert here("serve", "--port", "7481") == 0
    ((app, host, port, named),) = here.served
    assert isinstance(app, FastAPI) and (host, port, named) == ("127.0.0.1", 7481, True)
    assert here.said() == [f"SimMirror {__version__} on http://127.0.0.1:7481"]
    assert "config: " in here.err.getvalue() and "stream.fps" in here.err.getvalue()
    assert (tmp_path / "state" / "token").is_file() and not info_path(tmp_path / "run").exists()


def test_serve_removes_its_file_as_soon_as_the_app_has_shut_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uvicorn raises a SIGTERM it caught again once it has shut down, which ends the process before `serve` returns.
    async def nothing(self: Runtime) -> None:
        return None

    monkeypatch.setattr(Runtime, "start", nothing)
    monkeypatch.setattr(Runtime, "close", nothing)
    here = Terminal(tmp_path)
    seen: dict[str, bool] = {}

    async def serve(app: FastAPI, host: str, port: int) -> None:
        async with app.router.lifespan_context(app):
            seen["while serving"] = info_path(tmp_path / "run").exists()
        seen["once shut down"] = info_path(tmp_path / "run").exists()

    here.ctx.serve = serve
    assert here("serve", "--port", "7481") == 0
    assert seen == {"while serving": True, "once shut down": False}


def test_a_second_daemon_is_refused_and_detach_leaves_the_running_one(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    write_info(tmp_path / "run", DaemonInfo(os.getpid(), 7466, __version__))
    assert here("serve") == 1
    assert f"already running at http://127.0.0.1:7466 (pid {os.getpid()})" in here.err.getvalue()
    assert here("serve", "--detach") == 0 and here.served == [] and here.spawned == []


def test_detach_starts_the_daemon_in_the_background_with_the_same_settings(tmp_path: Path) -> None:
    here = Terminal(tmp_path, daemon=Daemon(up=False))
    config = str(tmp_path / "other.toml")
    assert here("serve", "--detach", "--port", "7466", "--config", config) == 0
    ((argv, log),) = here.spawned
    assert argv == (
        "/usr/bin/python3",
        "-m",
        "sim_mirror",
        "serve",
        "--foreground",
        "--port",
        "7466",
        "--config",
        config,
    )
    assert log == tmp_path / "logs" / "daemon.log"
    assert here.said() == [f"the SimMirror daemon is running at http://127.0.0.1:7466; its log is {log}"]


# -- commands that use the daemon ------------------------------------------------------------------------------------


def test_mcp_serves_this_projects_tools_through_the_daemon(tmp_path: Path) -> None:
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}}]
    here = Terminal(tmp_path, stdin="".join(json.dumps(message) + "\n" for message in messages))
    assert here("mcp", "--root", str(tmp_path / "Notes")) == 0
    assert json.loads(here.said()[0])["result"]["content"][0]["text"] == "ok"
    (minted,) = here.daemon.made("POST", "/api/v1/admin/tokens")
    assert minted["scopes"] == [Scope.for_folder(here.cwd).id] and minted["roots"] == [
        str((tmp_path / "Notes").resolve())
    ]
    named = Terminal(tmp_path / "named")
    assert named("mcp", "--scope", "demo") == 0
    (minted,) = named.daemon.made("POST", "/api/v1/admin/tokens")
    assert minted["scopes"] == ["demo"] and minted["roots"] == [str(named.cwd.resolve())]


def test_open_starts_the_daemon_and_opens_the_viewer_without_printing_the_code(tmp_path: Path) -> None:
    here = Terminal(tmp_path, daemon=Daemon(up=False))
    assert here("open", "--scope", "demo") == 0
    assert here.opened == ["http://127.0.0.1:7466/viewer/demo#code=c0de"] and len(here.spawned) == 1
    assert here.said() == ["opened the viewer for demo (http://127.0.0.1:7466/viewer/demo)"]
    printing = Terminal(tmp_path / "print")
    assert printing("open", "--scope", "demo", "--print") == 0 and printing.opened == []
    assert printing.said() == ["http://127.0.0.1:7466/viewer/demo#code=c0de"]
    no_browser = Terminal(tmp_path / "no-browser", browser=False)
    assert no_browser("open", "--scope", "demo") == 0 and no_browser.said() == [no_browser.opened[0]]


def test_devices_lists_this_macs_simulators_and_chooses_one_for_a_project(tmp_path: Path) -> None:
    listed = [
        {"udid": "U-1", "runtime": "iOS 26.5", "name": "iPhone 17 Pro", "state": "Booted", "created": False},
        {"udid": "U-2", "runtime": "iOS 26.5", "name": "SimMirror · demo", "state": "Shutdown", "created": True},
    ]
    here = Terminal(tmp_path, daemon=Daemon(devices=listed))
    assert here("devices", "--scope", "demo") == 0
    assert here("devices", "list", "--scope", "demo") == 0 and here.said()[:2] == here.said()[2:4]
    assert here.said()[:2] == [
        "U-1  iOS 26.5  iPhone 17 Pro  Booted",
        "U-2  iOS 26.5  SimMirror · demo  Shutdown  (made by SimMirror)",
    ]
    assert here("devices", "choose", "U-2", "--scope", "demo") == 0
    assert here.daemon.made("PUT", "/api/v1/scopes/demo/device") == [{"udid": "U-2"}]
    assert here.said()[-1] == "demo uses U-2 from now on"
    empty = Terminal(tmp_path / "empty")
    assert empty("devices") == 0 and empty.said() == ["no iOS simulators are available on this Mac"]


def test_a_refusal_from_the_daemon_is_one_line_and_exit_status_one(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    here.daemon.refuse.add("/api/v1/admin/login-codes")
    assert here("open", "--scope", "demo") == 1
    assert here.err.getvalue() == "sim-mirror: the daemon refused /api/v1/admin/login-codes: refused here\n"
    assert here("open", "--scope", "bad id") == 1 and "a scope id is 1 to 128" in here.err.getvalue()


# -- doctor ----------------------------------------------------------------------------------------------------------


def test_doctor_reports_what_the_checks_found_and_exits_with_their_verdict(tmp_path: Path) -> None:
    seen: list[DoctorContext] = []

    async def diagnose(ctx: DoctorContext) -> Report:
        seen.append(ctx)
        return Report(
            (CheckResult("mac", "ok", "macOS 26.6.2"), CheckResult("companion", "warn", "not installed", "brew"))
        )

    def registry(context: ConnectorContext) -> ConnectorRegistry:
        assert context.simctl_for("/Applications/Xcode.app/Contents/Developer").developer_dir.endswith("Developer")
        return ConnectorRegistry([FakeConnector("idb")])

    here = Terminal(tmp_path)
    here.ctx.diagnose, here.ctx.registry = diagnose, registry
    assert here("doctor", "--no-tap", "--json") == 2
    report = json.loads(here.out.getvalue())
    assert report["status"] == "warn" and [check["name"] for check in report["checks"]] == ["mac", "companion"]
    assert seen[0].runtime is None and seen[0].device is None
    tapping = Terminal(tmp_path / "tap")
    tapping.ctx.diagnose, tapping.ctx.registry = diagnose, registry
    assert tapping("doctor", "--device", "U-1") == 2
    assert tapping.said()[:2] == ["ok    mac: macOS 26.6.2", "warn  companion: not installed"]
    assert isinstance(seen[1].runtime, Runtime) and seen[1].device == "U-1"
    assert not seen[1].runtime.reaper.running


# -- config ----------------------------------------------------------------------------------------------------------


def test_config_says_where_it_is_and_changes_one_value_at_a_time(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here("config", "path") == 0 and here.said() == [str(tmp_path / "config.toml")]
    assert here("config", "set", "stream.fps", "24") == 0 and here.said()[-1] == "stream.fps = 24"
    assert "fps = 24" in (tmp_path / "config.toml").read_text()
    assert here("config", "get", "stream_fps") == 0 and here.said()[-1] == "24"
    assert here("config", "list") == 0
    listed = here.said()
    assert "stream.fps = 24" in listed and "enabled = true  # default" in listed
    here.env["SIM_MIRROR_STREAM_QUALITY"] = "60"
    assert here("config", "list") == 0 and "stream.quality = 60  # SIM_MIRROR_STREAM_QUALITY" in here.said()
    assert here("config", "set", "agent.cursor", "false", "--scope", "demo") == 0
    assert here("config", "get", "agent.cursor", "--scope", "demo") == 0 and here.said()[-1] == "false"
    assert here("config", "list", "--scope", "demo") == 0
    config_file = tmp_path / "config.toml"
    # The scope's own value says where it is; one the whole file sets does not pass for the environment's.
    assert f'agent.cursor = false  # [scopes."demo"] in {config_file}' in here.said()
    assert "stream.fps = 24" in here.said() and "stream.quality = 60  # SIM_MIRROR_STREAM_QUALITY" in here.said()
    assert here("config", "unset", "stream.fps") == 0 and here.said()[-1] == "stream.fps unset"
    assert (
        here("config", "unset", "stream.fps") == 0
        and here.said()[-1] == f"stream.fps was not set in {tmp_path / 'config.toml'}"
    )
    assert here("config", "validate") == 0 and here.said()[-1] == f"config ok: {tmp_path / 'config.toml'}"


def test_config_refuses_what_is_not_a_setting_or_a_value_and_says_what_is_wrong_with_the_file(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here("config", "set", "stream.fps", "999") == 1 and "stream.fps" in here.err.getvalue()
    assert here("config", "get", "stream.speed") == 1 and "stream.speed is not a setting" in here.err.getvalue()
    (tmp_path / "config.toml").write_text("[stream]\nfps = 999\nspeed = 1\n")
    assert here("config", "validate") == 1 and "stream.speed" in here.err.getvalue()


def test_a_running_daemon_applies_a_changed_setting_before_the_command_returns(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    write_info(tmp_path / "run", DaemonInfo(os.getpid(), 7466, __version__))
    assert here("config", "set", "enabled", "false") == 0
    assert here.daemon.made("POST", "/api/v1/admin/reload") == [{}]
    assert here.said()[-1] == "the daemon at http://127.0.0.1:7466 applied it"
    here.daemon.refuse.add("/api/v1/admin/reload")
    assert here("config", "unset", "enabled") == 0
    assert "the running daemon did not reload" in here.err.getvalue()
    assert read_info(tmp_path / "run") is not None


# -- token -----------------------------------------------------------------------------------------------------------


def test_tokens_are_made_once_listed_and_revoked(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here("token", "list") == 0 and here.said() == ["no scoped tokens"]
    assert here("token", "create", "--kind", "agent", "--scope", "demo", "--label", "ci", "--root", "~/Notes") == 0
    token = here.said()[-1]
    record = here.ctx.tokens().match(token)
    assert record is not None and record.roots == (str((Path("~/Notes").expanduser()).resolve()),)
    assert f"made agent token {record.id} for demo; it is shown only this once" in here.err.getvalue()
    assert here("token", "list") == 0 and here.said()[-1] == f"{record.id}  agent  demo  ci"
    assert here("token", "revoke", record.id) == 0 and here.said()[-1] == f"revoked {record.id}"
    assert here("token", "revoke", record.id) == 1 and f"there is no token {record.id}" in here.err.getvalue()
    assert here("token", "create", "--kind", "viewer", "--scope", "bad id") == 1
    assert "a token is for one or more scope ids" in here.err.getvalue()


def test_the_daemons_address_comes_from_its_file_or_else_the_settings(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here.ctx.daemon_url() == "http://127.0.0.1:7466"
    write_info(tmp_path / "run", DaemonInfo(os.getpid(), 7482, __version__))
    assert here.ctx.client().url == "http://127.0.0.1:7482"
    assert here.ctx.scope(None) == Scope.for_folder(here.cwd) and asyncio.iscoroutinefunction(here.ctx.serve)
