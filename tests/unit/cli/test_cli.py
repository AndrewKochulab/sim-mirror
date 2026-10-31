# SPDX-License-Identifier: Apache-2.0
"""The ``sim-mirror`` command line, with no daemon, process, server, browser or Xcode: every command against a daemon
answered in memory, and a refusal a person can act on as one line and exit status 1."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import sim_mirror.__main__ as module_entry
from sim_mirror import api
from sim_mirror._version import __version__
from sim_mirror.cli import app_command
from sim_mirror.cli import context as context_module
from sim_mirror.cli import record as record_command
from sim_mirror.cli import stop as stop_command
from sim_mirror.cli.context import CliContext, serve_with_uvicorn
from sim_mirror.cli.main import main, parser
from sim_mirror.cli.version import connector_names
from sim_mirror.connectors.mcpbridge.client import BridgeClient
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.core.recordings import STOP_WAIT_S
from sim_mirror.core.runtime import Runtime
from sim_mirror.daemon.lifecycle import DaemonInfo, info_path, mark_stopped, read_info, stopped_mark, write_info
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.report import CheckResult, Report
from sim_mirror.host_copy import HostCopy
from sim_mirror.mcp import launcher
from sim_mirror.platform.device_data import DEVICES_DIR_ENV
from sim_mirror.protocol import PROTOCOL_VERSION
from sim_mirror.scope import Scope
from sim_mirror.testing.app_sdk import FakeAppSdk, app_hierarchy, app_node, write_listing
from sim_mirror.testing.daemon import Daemon
from sim_mirror.testing.fakes import BOOTED_UDID, FakeBridge, FakeConnector, FakeProcess, FakeXcrun


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
    assert here.said()[-3:] == [
        f"sim-mirror {__version__}",
        f"protocol v{PROTOCOL_VERSION}",
        "connectors: android, idb, iphone, mcpbridge, native, simctl",
    ]
    extra = connector_names(entry_points=lambda group: [SimpleNamespace(name="android")])
    assert extra == ["android", "idb", "iphone", "mcpbridge", "native", "simctl"]


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
    assert here.said()[0] == "sim_device: Your iOS Simulator, a real iPhone or iPad, or an Android emulator or phone."
    assert all(not line.startswith("sim_build_run") for line in here.said())
    here.out.truncate(0)
    here.out.seek(0)
    assert here("tools", "--json") == 0
    assert [tool["name"] for tool in json.loads(here.out.getvalue())["tools"]][:2] == ["sim_device", "sim_snapshot"]


# -- serve -----------------------------------------------------------------------------------------------------------


async def unauthenticated(app: FastAPI, path: str, *, port: int) -> int:
    """The status a request with no credential gets from the app, sent as the daemon's own host."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{port}") as http:
        return (await http.get(path)).status_code


def test_serve_runs_the_daemon_here_and_says_where_while_its_file_names_it(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    (tmp_path / "config.toml").write_text("[stream]\nfps = 999\n")
    mark_stopped(tmp_path / "run")
    assert here("serve", "--port", "7481") == 0
    assert stopped_mark(tmp_path / "run") is None, "running again, it is no longer stopped on purpose"
    ((app, host, port, named),) = here.served
    assert isinstance(app, FastAPI) and (host, port, named) == ("127.0.0.1", 7481, True)
    # The settings panel's routes are served over this config.toml: asked without a credential, they ask for one.
    assert asyncio.run(unauthenticated(app, "/api/v1/scopes/demo/settings", port=7481)) == 401
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
    assert here.daemon.made("POST", "/api/v1/admin/login-codes") == [{"scope": "demo", "settings": False}]
    settings = Terminal(tmp_path / "settings")
    assert settings("open", "--scope", "demo", "--settings") == 0
    assert settings.daemon.made("POST", "/api/v1/admin/login-codes") == [{"scope": "demo", "settings": True}]


def test_settings_confirm_shows_each_change_waiting_and_its_code_or_that_none_is(tmp_path: Path) -> None:
    waiting = [
        {"id": "a", "scope": "demo", "summary": "demo: build.tools = true", "code": "ABCD-EFGH", "expires_in_s": 290.0},
        {"id": "b", "scope": "demo", "summary": "demo: device.developer_dir put back", "code": "JKMN-PQRT",
         "expires_in_s": 20.0},
    ]  # fmt: skip
    here = Terminal(tmp_path, daemon=Daemon(pending=waiting))
    assert here("settings", "confirm") == 0
    assert here.said() == [
        "demo: build.tools = true",
        "  code ABCD-EFGH  (enter it in the page within 5 min, only if you asked for this)",
        "demo: device.developer_dir put back",
        "  code JKMN-PQRT  (enter it in the page within 1 min, only if you asked for this)",
    ]
    assert here.daemon.made("POST", "/api/v1/admin/settings-confirmations") == [{}]
    nothing = Terminal(tmp_path / "nothing")
    assert nothing("settings", "confirm") == 0 and nothing.said() == ["No settings change is waiting to be confirmed."]


def test_devices_lists_this_macs_simulators_and_chooses_one_for_a_project(tmp_path: Path) -> None:
    listed = [
        {"udid": "U-1", "runtime": "iOS 26.5", "name": "iPhone 17 Pro", "state": "Booted", "created": False},
        {"udid": "U-2", "runtime": "iOS 26.5", "name": "SimMirror · demo", "state": "Shutdown", "created": True},
        {"udid": "P-1", "runtime": "iOS 26.3", "name": "Test iPhone", "state": "Connected", "created": False,
         "kind": "physical", "connection": "usb", "detail": None},
        {"udid": "P-2", "runtime": "iOS 27.0", "name": "Second iPhone", "state": "Disconnected", "created": False,
         "kind": "physical", "connection": None, "detail": "Not connected"},
        {"udid": "avd-Pixel-9-fa39dd", "runtime": "Android 16 (API 36)", "name": "Pixel 9", "state": "Booted",
         "created": False, "kind": "android", "connection": None, "detail": None},
        {"udid": "adb-R58M12ABC-9b1e04", "runtime": "Android 15 (API 35)", "name": "Pixel 8", "state": "Connected",
         "created": False, "kind": "android", "connection": "usb", "detail": None},
    ]  # fmt: skip
    here = Terminal(tmp_path, daemon=Daemon(devices=listed))
    assert here("devices", "--scope", "demo") == 0
    assert here("devices", "list", "--scope", "demo") == 0 and here.said()[:6] == here.said()[6:12]
    assert here.said()[:6] == [
        "U-1  iOS 26.5  iPhone 17 Pro  Booted",
        "U-2  iOS 26.5  SimMirror · demo  Shutdown  (made by SimMirror)",
        "P-1  iOS 26.3  Test iPhone  Connected  real device, usb",
        "P-2  iOS 27.0  Second iPhone  Disconnected  real device, not connected  (Not connected)",
        "avd-Pixel-9-fa39dd  Android 16 (API 36)  Pixel 9  Booted  Android emulator",
        "adb-R58M12ABC-9b1e04  Android 15 (API 35)  Pixel 8  Connected  Android phone, usb",
    ]
    assert here("devices", "choose", "U-2", "--scope", "demo") == 0
    assert here.daemon.made("PUT", "/api/v1/scopes/demo/device") == [{"udid": "U-2"}]
    assert here.said()[-1] == "demo uses U-2 from now on"
    empty = Terminal(tmp_path / "empty")
    nothing = "no iOS simulators, real devices or Android devices are available on this Mac"
    assert empty("devices") == 0 and empty.said() == [nothing]


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


def test_config_copy_gives_a_scope_another_ones_settings_keeping_what_it_sets_already(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '# mine\n[scopes."main"]\nbuild.tools = true\nagent.cursor = false\nserver.port = 7500\n'
        '[scopes."wt"]\nagent.cursor = true\n'
    )
    assert here("config", "copy", "--from", "main", "--scope", "wt") == 0
    assert here.said() == [
        "agent.cursor kept: wt sets it already",
        "build.tools = true  # copied from main",
    ]
    written = config_file.read_text()
    assert written.startswith("# mine") and here.ctx.config().get(Scope.named("wt")).build_tools
    assert here.ctx.config().get(Scope.named("wt")).agent_cursor
    # A setting only the whole daemon reads is not a scope's to copy.
    assert "server" not in written.split('[scopes."wt"]')[1]
    assert here("config", "copy", "--from", "main", "--scope", "wt") == 0
    assert here.said()[-1] == "nothing copied: main sets nothing of its own that wt does not"
    assert config_file.read_text() == written
    assert here("config", "copy", "--from", "main", "--scope", "main") == 1
    assert "not into its own" in here.err.getvalue()
    assert here("config", "copy", "--from", "bad id", "--scope", "wt") == 1
    assert "'bad id' is not a scope id" in here.err.getvalue()


def test_config_scope_prints_a_folders_scope_id_alone_so_a_script_can_pass_it_on(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here("config", "scope") == 0 and here.said() == [Scope.for_folder(here.cwd).id]
    other = tmp_path / "Other App"
    other.mkdir()
    assert here("config", "scope", "--folder", str(other)) == 0
    assert here.said()[-1] == Scope.for_folder(other).id and here.said()[-1].startswith("project-Other-App-")
    # A relative folder is the one in the folder the command runs in.
    (here.cwd / "sub").mkdir()
    assert here("config", "scope", "--folder", "sub") == 0 and here.said()[-1] == Scope.for_folder(here.cwd / "sub").id
    assert here("config", "scope", "--folder", "missing") == 1
    assert here.err.getvalue() == "sim-mirror: missing is not a folder\n" and len(here.said()) == 3


def test_doctor_looks_at_the_folder_it_runs_in_and_the_config_file_it_reads(tmp_path: Path) -> None:
    seen: list[DoctorContext] = []

    async def diagnose(ctx: DoctorContext) -> Report:
        seen.append(ctx)
        return Report(())

    here = Terminal(tmp_path)
    here.ctx.diagnose = diagnose
    here.ctx.registry = lambda context: ConnectorRegistry([FakeConnector("idb")])
    assert here("doctor", "--no-tap") == 0
    assert seen[0].folder == here.cwd and seen[0].config_file == tmp_path / "config.toml"


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
    assert here("token", "create", "--kind", "host", "--scope", "notes:*", "--label", "Notes") == 0
    host = here.ctx.tokens().match(here.said()[-1])
    assert host is not None and host.namespaces == ("notes",)
    made, _ = here.ctx.tokens().create("agent", ["notes:1"], host=host)
    assert here("token", "list") == 0
    assert here.said()[-2:] == [
        f"{host.id}  host  notes:*  Notes",
        f"{made.id}  agent  notes:1  (made by host {host.id})",
    ]
    assert here("token", "revoke", host.id) == 0
    assert here.said()[-1] == f"revoked {host.id}, and the tokens it made: {made.id}"


def test_the_daemons_address_comes_from_its_file_or_else_the_settings(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    assert here.ctx.daemon_url() == "http://127.0.0.1:7466"
    write_info(tmp_path / "run", DaemonInfo(os.getpid(), 7482, __version__))
    assert here.ctx.client().url == "http://127.0.0.1:7482"
    assert here.ctx.scope(None) == Scope.for_folder(here.cwd) and asyncio.iscoroutinefunction(here.ctx.serve)


# -- xcode -----------------------------------------------------------------------------------------------------------


def with_bridge(here: Terminal, bridge: FakeBridge) -> list[str]:
    """Point the terminal's bridge at a fake one; answers the Xcode each bridge was started for."""
    started: list[str] = []

    def make(developer_dir: str) -> BridgeClient:
        started.append(developer_dir)
        return BridgeClient(developer_dir, spawn=bridge.spawn)

    here.ctx.bridge = make
    return started


def project(folder: Path, name: str = "App.xcodeproj") -> Path:
    path = folder / name
    path.mkdir(parents=True)
    return path


def test_approving_opens_this_folders_project_through_xcodes_tools_and_closes_it_again(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    app = project(here.cwd)
    (tmp_path / "config.toml").write_text('[device]\ndeveloper_dir = "/Applications/Xcode27.app/Contents/Developer"\n')
    bridge = FakeBridge(folder=tmp_path / "artifacts")
    started = with_bridge(here, bridge)
    assert here("xcode", "approve") == 0
    assert here.said() == ["Xcode approved SimMirror to use its tools, opening App.xcodeproj"]
    assert bridge.calls == [
        ("XcodeListWorkspaces", {}),
        ("XcodeOpenWorkspace", {"path": str(app)}),
        ("XcodeCloseWorkspace", {"workspaceIdentifier": "w0"}),
    ]
    assert started == ["/Applications/Xcode27.app/Contents/Developer"] and bridge.processes[0].returncode is not None


def test_a_project_a_person_already_has_open_stays_open_and_so_does_one_when_xcode_will_not_list(
    tmp_path: Path,
) -> None:
    here = Terminal(tmp_path)
    app = project(here.cwd / "ios", "Notes.xcworkspace")
    bridge = FakeBridge(folder=tmp_path / "artifacts")
    bridge.open_workspaces.append(str(app))
    with_bridge(here, bridge)
    assert here("xcode", "approve", "ios/Notes.xcworkspace") == 0
    assert bridge.tools() == ["XcodeListWorkspaces", "XcodeOpenWorkspace"]
    other = FakeBridge(folder=tmp_path / "other").refuse("XcodeListWorkspaces", "This agent isn't approved yet.")
    with_bridge(here, other)
    assert here("xcode", "approve", "ios/Notes.xcworkspace", "--scope", "notes") == 0
    assert other.tools() == ["XcodeListWorkspaces", "XcodeOpenWorkspace"]
    assert here("xcode") == 1
    assert "exactly one" in here.err.getvalue()


def test_approving_without_a_project_to_open_or_with_xcode_refusing_says_why(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    bridge = FakeBridge(folder=tmp_path / "artifacts")
    with_bridge(here, bridge)
    assert here("xcode", "approve") == 1
    assert here.err.getvalue().splitlines()[-1] == (
        f"sim-mirror: name the .xcodeproj or .xcworkspace to open, since {here.cwd} does not have exactly one"
    )
    assert here("xcode", "approve", "README.md") == 1
    assert here.err.getvalue().splitlines()[-1] == (
        "sim-mirror: name the .xcodeproj or .xcworkspace to open: README.md is not one"
    )
    project(here.cwd)
    project(here.cwd, "Other.xcodeproj")
    assert here("xcode", "approve", "App.xcodeproj") == 0
    bridge.refuse("XcodeOpenWorkspace", "The project could not be opened.")
    assert here("xcode", "approve", "App.xcodeproj") == 1
    assert here.err.getvalue().splitlines()[-1] == (
        "sim-mirror: Xcode refused XcodeOpenWorkspace: The project could not be opened."
    )
    bridge.stop('xcrun: error: unable to find utility "mcpbridge", not a developer tool or in PATH')
    assert here("xcode", "approve", "App.xcodeproj") == 1
    assert "needs Xcode 27 or later" in here.err.getvalue().splitlines()[-1]
    bridge.stop("Fatal error: something else")
    assert here("xcode", "approve", "App.xcodeproj") == 1
    assert here.err.getvalue().splitlines()[-1] == "sim-mirror: mcpbridge stopped: Fatal error: something else"


# -- app -------------------------------------------------------------------------------------------------------------


def with_apps(here: Terminal, root: Path) -> Path:
    """Simulators whose data is under `root`, booted as a real Mac listed them; answers the booted one's data folder."""
    here.env[DEVICES_DIR_ENV] = str(root)
    here.ctx.xcrun = FakeXcrun().with_lists()
    return root / BOOTED_UDID / "data"


def app_args(*argv: str) -> Any:
    return parser().parse_args(["app", "hierarchy", *argv])


def test_no_app_sharing_a_hierarchy_or_no_simulator_to_read_says_so_and_exits_one(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    with_apps(here, tmp_path / "devices")
    assert here("app") == 1
    assert here.err.getvalue().splitlines()[-1].startswith(f"sim-mirror: {BOOTED_UDID}: no app on it shares")
    assert here("app", "hierarchy", "--json") == 1
    assert json.loads(here.out.getvalue())["app"] is None
    assert here("app", "hierarchy", "--device", "booted") == 1
    assert here.err.getvalue().splitlines()[-1] == "sim-mirror: 'booted' is not a simulator's UDID"
    here.ctx.xcrun = FakeXcrun().on("simctl", "list", "devices", "-j", out='{"devices": {}}')
    assert here("app", "hierarchy") == 1
    assert (
        here.err.getvalue().splitlines()[-1] == "sim-mirror: no simulator is booted: boot one, or name it with --device"
    )
    two = {"devices": {"iOS 26.5": [{"udid": "U-1", "name": "A", "state": "Booted", "isAvailable": True},
                                    {"udid": "U-2", "name": "B", "state": "Booted", "isAvailable": True}]}}  # fmt: skip
    here.ctx.xcrun = FakeXcrun().on("simctl", "list", "devices", "-j", out=json.dumps(two))
    assert here("app", "hierarchy") == 1
    assert here.err.getvalue().splitlines()[-1] == (
        "sim-mirror: 2 simulators are booted: name one with --device (U-1, U-2)"
    )
    here.ctx.xcrun = FakeXcrun().on("simctl", "list", "devices", "-j", rc=1, err="simctl failed")
    assert here("app", "hierarchy") == 1
    assert "simctl failed" in here.err.getvalue().splitlines()[-1]


async def test_the_app_in_front_prints_what_an_agent_will_read_of_it(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    data = with_apps(here, tmp_path / "devices")
    (tmp_path / "config.toml").write_text("[connectors.app]\nmax_nodes = 100\n")
    document = app_hierarchy(
        app_node("button", None, (340, 60, 44, 44), label_source=None),
        app_node("button", "trash", (340, 60, 44, 44)),
        app_node("text", "Hello"),
        truncated=True,
        notes=["swiftui debug data unavailable: unexpected shape"],
    )
    async with FakeAppSdk(document) as app:
        write_listing(data, app.listing(BOOTED_UDID))
        assert await app_command._hierarchy(app_args(), here.ctx) == 0
        said = here.said()
        assert said[0].startswith(f"AppSDK · {app.bundle_id} · SDK 1.0.0 · 3 views of {BOOTED_UDID} · read in ")
        assert said[1:] == [
            'e1 button "trash" (362,82)',
            'e2 text "Hello" (22,22)',
            HostCopy().app_hierarchy_cut("AppSDK", 100),
            "swiftui debug data unavailable: unexpected shape",
        ]
        assert app.requests[-1].line == "GET /v1/hierarchy?max_nodes=100 HTTP/1.1"
        here.out.truncate(0)
        here.out.seek(0)
        assert await app_command._hierarchy(app_args("--json", "--device", BOOTED_UDID), here.ctx) == 0
        printed = json.loads(here.out.getvalue())
        assert app.secret not in here.out.getvalue()
        assert {key: printed[key] for key in ("device", "app", "protocol", "views", "truncated", "listed")} == {
            "device": BOOTED_UDID,
            "app": {"name": "AppSDK", "bundle_id": app.bundle_id, "sdk_version": "1.0.0"},
            "protocol": 1,
            "views": 3,
            "truncated": True,
            "listed": [app.bundle_id],
        }
        assert printed["elements"][1]["label"] == "trash" and printed["unread"] == []
        app.document = app_hierarchy(app_node(), screen=None)
        here.out.truncate(0)
        here.out.seek(0)
        assert await app_command._hierarchy(app_args(), here.ctx) == 0
        assert here.said()[1] == 'e1 button "Go" (22,22)'


async def test_an_app_that_cannot_be_read_or_is_not_in_front_says_why_and_exits_one(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    data = with_apps(here, tmp_path / "devices")
    async with FakeAppSdk() as app:
        write_listing(data, app.listing(BOOTED_UDID))
        app.answer = "refuse"
        assert await app_command._hierarchy(app_args(), here.ctx) == 1
        assert here.err.getvalue().splitlines()[-1] == (
            "sim-mirror: AppSDK shares its view hierarchy, but this snapshot could not read it: "
            "AppSDK refused to share its view hierarchy (503 busy)"
        )
        assert await app_command._hierarchy(app_args("--json"), here.ctx) == 1
        assert json.loads(here.out.getvalue())["unread"] == [
            {
                "app": "AppSDK",
                "bundle_id": app.bundle_id,
                "reason": "AppSDK refused to share its view hierarchy (503 busy)",
            }
        ]
        app.answer = "inactive"
        assert await app_command._hierarchy(app_args(), here.ctx) == 1
        assert here.err.getvalue().splitlines()[-1] == (
            f"sim-mirror: 1 app shares a view hierarchy on {BOOTED_UDID}, and none is in front"
        )
        async with FakeAppSdk(bundle_id="com.example.other") as other:
            other.answer = "inactive"
            write_listing(data, other.listing(BOOTED_UDID))
            assert await app_command._hierarchy(app_args(), here.ctx) == 1
    assert here.err.getvalue().splitlines()[-1] == (
        f"sim-mirror: 2 apps share a view hierarchy on {BOOTED_UDID}, and none is in front"
    )


RECORDING = {
    "id": "20260929-034515-test-iphone",
    "device": "Test iPhone",
    "started_at": "2026-09-29T00:45:15Z",
    "duration_ms": 10800,
    "files": [
        {"name": "r.mp4", "path": "/r/r.mp4", "format": "mp4", "bytes": 4_300_000, "width": 900, "height": 1952},
    ],
    "notes": [],
}


def test_record_starts_stops_and_lists_through_the_daemon(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = "/api/v1/scopes/demo"
    here = Terminal(tmp_path)
    here.daemon.answers.update({
        ("POST", f"{base}/recording"): {"recording": {"id": "r", "since_ms": 0, "by": "person", "max_ms": 300000}},
        ("DELETE", f"{base}/recording"): {"recording": RECORDING},
        ("GET", f"{base}/recordings"): {"recordings": [RECORDING]},
    })  # fmt: skip
    assert here("record", "start", "--format", "gif", "--scope", "demo") == 0
    assert here.daemon.made("POST", f"{base}/recording") == [
        {"format": "gif", "width": None, "quality": None, "preset": None, "also": None}
    ]
    assert here.said()[-1] == "recording demo (by person); `sim-mirror record stop` keeps it"
    assert here("record", "start", "--preset", "pr", "--width", "600", "--quality", "medium", "--scope", "demo") == 0
    assert here.daemon.made("POST", f"{base}/recording")[-1] == {
        "format": None,
        "width": 600,
        "quality": "medium",
        "preset": "pr",
        "also": None,
    }
    assert here("record", "start", "--also", "pr", "--scope", "demo") == 0
    assert here.daemon.made("POST", f"{base}/recording")[-1]["also"] == "pr"
    assert here("record", "start", "--fps", "120", "--scope", "demo") == 0
    assert here.daemon.made("POST", f"{base}/recording")[-1]["fps"] == 120
    with pytest.raises(SystemExit):
        here("record", "start", "--fps", "24", "--scope", "demo")
    assert "argument --fps: invalid choice" in capsys.readouterr().err
    for wrong in ("100", "wide"):
        with pytest.raises(SystemExit):
            here("record", "start", "--width", wrong, "--scope", "demo")
        assert "a whole number of pixels from 240 to 1200" in capsys.readouterr().err
    assert here("record", "stop", "--scope", "demo") == 0
    assert here.said()[-2:] == [
        "kept 20260929-034515-test-iphone · Test iPhone · 10.8s",
        "mp4 900x1952 · 4.3 MB · /r/r.mp4",
    ]
    assert here("record", "--scope", "demo") == 0 and here.said()[-1] == "mp4 900x1952 · 4.3 MB · /r/r.mp4"
    here.daemon.answers[("GET", f"{base}/recordings")] = {"recordings": []}
    assert here("record", "list", "--scope", "demo") == 0 and here.said()[-1] == "no recordings kept yet"
    here.daemon.refuse.add(f"{base}/recording")
    assert here("record", "stop", "--scope", "demo") == 1
    assert here.err.getvalue().endswith(f"the daemon refused {base}/recording: refused here\n")


@dataclass
class TimedDaemon(Daemon):
    """The daemon, noting how long each request was given."""

    timeouts: list[tuple[str, str, float]] = field(default_factory=list)

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.timeouts.append((request.get_method(), request.full_url.split("7466", 1)[-1], timeout))
        return super().__call__(request, timeout)


def test_record_names_a_recording_bounds_it_and_waits_for_a_long_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "/api/v1/scopes/demo"
    daemon = TimedDaemon()
    here = Terminal(tmp_path, daemon=daemon)
    rendering = {
        "recording": {**RECORDING, "files": [], "notes": ["still being rendered; it will be kept at /r/r.mp4"]},
        "recorded_ms": 2_400_000,
        "stopped_by_itself": True,
        "rendering": True,
    }
    finishing = {
        "id": "r",
        "device": "Test iPhone",
        "started_at": "2026-09-29T00:45:15Z",
        "recorded_ms": 2_400_000,
        "stopped_by_itself": True,
        "paths": ["/r/r.mp4"],
    }
    here.daemon.answers.update({
        ("POST", f"{base}/recording"): {"recording": {"id": "r", "since_ms": 0, "by": "person", "max_ms": 2400000}},
        ("DELETE", f"{base}/recording"): rendering,
        ("GET", f"{base}/recordings"): {"recordings": [], "recording": None, "finishing": [finishing]},
    })  # fmt: skip
    assert here("record", "start", "--max-seconds", "2400", "--label", "Firmware update", "--scope", "demo") == 0
    asked = here.daemon.made("POST", f"{base}/recording")[-1]
    assert (asked["max_seconds"], asked["label"]) == (2400, "Firmware update")
    for flag, wrong, said in (
        ("--max-seconds", "3601", "a whole number of seconds from 5 to 3600"),
        ("--max-seconds", "long", "a whole number of seconds from 5 to 3600"),
        ("--label", "?!", "up to 64 characters, with a letter or a digit"),
    ):
        with pytest.raises(SystemExit):
            here("record", "start", flag, wrong, "--scope", "demo")
        assert said in capsys.readouterr().err
    assert here("record", "stop", "--scope", "demo") == 0
    assert here.said()[-3:] == [
        "stopped by itself at 2400s, its limit; still rendering 20260929-034515-test-iphone · Test iPhone · 10.8s",
        "note: still being rendered; it will be kept at /r/r.mp4",
        "`sim-mirror record list` says when it is kept",
    ]
    [(_, _, waited)] = [timed for timed in daemon.timeouts if timed[:2] == ("DELETE", f"{base}/recording")]
    assert waited == record_command.STOP_TIMEOUT_S and waited > STOP_WAIT_S > launcher.REQUEST_TIMEOUT_S
    assert {timeout for method, _, timeout in daemon.timeouts if method != "DELETE"} == {launcher.REQUEST_TIMEOUT_S}
    assert here("record", "list", "--scope", "demo") == 0
    assert here.said()[-1] == "rendering r · Test iPhone (stopped by itself at 2400s); it will be kept at /r/r.mp4"


@pytest.mark.parametrize(
    ("argv", "asked"),
    [
        (("appearance", "dark"), {"action": "appearance", "mode": "dark"}),
        (("status-bar", "demo"), {"action": "status_bar", "preset": "demo"}),
        (("location", "50.45", "30.52"), {"action": "location", "latitude": 50.45, "longitude": 30.52}),
        (("clear-location",), {"action": "clear_location"}),
        (("text-size", "large"), {"action": "text_size", "size": "large"}),
        (("contrast", "on"), {"action": "contrast", "on": True}),
        (("reduce-motion", "off"), {"action": "reduce_motion", "on": False}),
    ],
)
def test_device_asks_the_daemon_for_the_change_as_sim_device_names_it(
    tmp_path: Path, argv: tuple[str, ...], asked: dict[str, Any]
) -> None:
    here = Terminal(tmp_path)
    here.daemon.answers[("POST", "/api/v1/scopes/demo/device/settings")] = {"said": "done"}
    assert here("device", *argv, "--scope", "demo") == 0
    assert here.daemon.made("POST", "/api/v1/scopes/demo/device/settings") == [asked]
    assert here.said()[-1] == "demo: done"


def test_an_older_daemon_is_asked_to_stop_by_the_pid_it_wrote_down(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    signalled: list[tuple[int, int]] = []
    here.ctx.kill = lambda pid, sig: signalled.append((pid, sig))
    assert here.ctx.retire_daemon() is False and signalled == [], "no daemon wrote itself down"
    write_info(here.root / "run", DaemonInfo(pid=os.getpid(), port=7466, version="1.2.0"))
    assert here.ctx.retire_daemon() is True and signalled == [(os.getpid(), signal.SIGTERM)]


# -- stop ------------------------------------------------------------------------------------------------------------

DEMO = "/api/v1/scopes/demo"


def stopping(tmp_path: Path) -> tuple[Terminal, list[tuple[int, int]]]:
    """A terminal whose daemon wrote down its pid and stops when it is signalled."""
    here = Terminal(tmp_path)
    write_info(here.root / "run", DaemonInfo(pid=os.getpid(), port=7466, version=__version__))
    signalled: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        signalled.append((pid, sig))
        here.daemon.up = False

    here.ctx.kill = kill
    return here, signalled


@pytest.mark.parametrize(
    ("argv", "answer", "query", "line"),
    [
        ((), {"stopped": True, "shut_down": False}, "", "demo: stopped; the device keeps running (--shutdown shuts"),
        (("--shutdown",), {"stopped": True, "shut_down": True}, "shutdown=true", "demo: stopped, and its simulator is"),
        (
            ("--shutdown",),
            {"stopped": True, "shut_down": True, "kind": "android"},
            "shutdown=true",
            "demo: stopped, and its emulator is",
        ),
        (("--shutdown",), {"stopped": True, "shut_down": False}, "shutdown=true", "demo: stopped; a real device is"),
        (("--shutdown",), {"stopped": True}, "shutdown=true", "demo: stopped, and its simulator is shut down"),
        ((), {"stopped": False}, "", "demo: no device was running"),
    ],
)
def test_stop_lets_go_of_this_scopes_device_only_and_says_what_became_of_it(
    tmp_path: Path, argv: tuple[str, ...], answer: dict[str, Any], query: str, line: str
) -> None:
    here, signalled = stopping(tmp_path)
    here.daemon.answers[("DELETE", DEMO)] = answer
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": []}
    assert here("stop", "--scope", "demo", *argv) == 0
    assert here.said()[-1].startswith(line), here.said()
    assert [asked for asked in here.daemon.queries if asked[0] == "DELETE"] == [("DELETE", DEMO, query)]
    assert signalled == [] and here.daemon.up and here.spawned == []


def test_stop_shutdown_refuses_while_another_scope_shares_the_simulator_unless_forced(tmp_path: Path) -> None:
    here, signalled = stopping(tmp_path)
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": True, "shut_down": True}
    served = [
        {"id": "demo", "device": "iPhone 17", "udid": BOOTED_UDID, "agent": False, "building": False},
        {"id": "tp-2", "device": "iPhone 17", "udid": BOOTED_UDID, "agent": True, "building": False},
        {"id": "tp-3", "device": "iPhone 17", "udid": "OTHER-UDID", "agent": False, "building": False},
        {"id": "tp-4", "device": None, "udid": None, "agent": True, "building": False},
    ]
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": served}
    assert here("stop", "--shutdown", "--scope", "demo") == 1
    assert here.err.getvalue() == (
        "sim-mirror: demo's simulator is also used by tp-2, so it is not shut down: shutting it down would end their "
        "session too. Leave out --shutdown to stop only this one, or add --force\n"
    )
    assert here.daemon.made("DELETE", DEMO) == [], "nothing was stopped"
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": True, "shut_down": False}
    assert here("stop", "--scope", "demo") == 0, "without --shutdown only this scope lets go"
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": True, "shut_down": True}
    assert here.said()[-2:] == [
        "demo: stopped; the device keeps running (--shutdown shuts a simulator down too)",
        f"{BOOTED_UDID} (iPhone 17) is still held by 1 other scope: tp-2 (an agent); use "
        f"`sim-mirror stop --device {BOOTED_UDID}` to let it go for all of them, or --daemon",
    ]
    assert here.daemon.made("GET", "/api/v1/admin/in-use") == [None, None]
    assert here("stop", "--shutdown", "--force", "--scope", "demo") == 0
    assert here.said()[-1] == "demo: stopped, and its simulator is shut down"
    assert [asked for asked in here.daemon.queries if asked[0] == "DELETE"][-1] == ("DELETE", DEMO, "shutdown=true")
    assert here.daemon.made("GET", "/api/v1/admin/in-use") == [None, None], "forced, nothing else is asked"
    alone, _ = stopping(tmp_path / "alone")
    alone.daemon.answers[("DELETE", DEMO)] = {"stopped": False}
    alone.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": served[1:]}
    assert alone("stop", "--shutdown", "--scope", "demo") == 0, "a scope running nothing shares nothing"
    assert signalled == []


def test_stop_for_a_scope_that_held_nothing_names_the_devices_other_scopes_still_hold(tmp_path: Path) -> None:
    here, _ = stopping(tmp_path)
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": False}
    phone = "00008120-0011223344556677"
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {
        "scopes": [
            {"id": "tp-2", "device": "iPhone A Test", "udid": phone, "agent": True, "building": False},
            {"id": "tp-3", "device": "iPhone A Test", "udid": phone, "agent": False, "building": False},
            {"id": "tp-4", "device": "iPhone 17", "udid": BOOTED_UDID, "agent": True, "building": False},
            {"id": "tp-5", "device": None, "udid": None, "agent": True, "building": False},
        ]
    }
    assert here("stop", "--scope", "demo") == 0
    assert here.said() == [
        "demo: no device was running",
        f"{phone} (iPhone A Test) is still held by 2 scopes: tp-2 (an agent), tp-3; use "
        f"`sim-mirror stop --device {phone}` to let it go for all of them, or --daemon",
        f"{BOOTED_UDID} (iPhone 17) is still held by 1 scope: tp-4 (an agent); use "
        f"`sim-mirror stop --device {BOOTED_UDID}` to let it go for all of them, or --daemon",
    ]


def test_stop_device_lets_it_go_for_every_scope_and_names_who_held_it(tmp_path: Path) -> None:
    here, signalled = stopping(tmp_path)
    phone = "00008120-0011223344556677"
    let_go = f"/api/v1/admin/devices/{phone}"
    here.daemon.answers[("DELETE", let_go)] = {
        "udid": phone,
        "stopped": True,
        "scopes": [{"id": "tp-2", "agents": ["sim-mirror mcp (pid 52260)"]}, {"id": "tp-3", "agents": []}],
    }
    assert here("stop", "--device", phone) == 0
    assert here.said() == [
        f"{phone}: let go for 2 scopes; the device keeps running",
        "  tp-2, held by sim-mirror mcp (pid 52260)",
        "  tp-3",
    ]
    assert here.daemon.made("DELETE", DEMO) == [] and here.daemon.made("DELETE", let_go) == [None]
    here.daemon.answers[("DELETE", let_go)] = {"udid": phone, "stopped": True, "scopes": []}
    assert here("stop", "--device", phone) == 0
    assert here.said()[-1] == f"{phone}: let go; no scope held it, and the device keeps running"
    here.daemon.answers[("DELETE", let_go)] = {"udid": phone, "stopped": False, "scopes": []}
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": []}
    assert here("stop", "--device", phone, "--daemon") == 0
    assert here.said()[-2:] == [
        f"{phone}: SimMirror was not running it, so there was nothing to let go",
        "stopped the SimMirror daemon; the devices it did not shut down keep running",
    ]
    assert signalled
    refused = Terminal(tmp_path / "refused")
    assert refused("stop", "--device", phone, "--shutdown") == 2
    assert "does not go with --shutdown" in refused.err.getvalue() and refused.daemon.requests == []


def test_stop_with_no_daemon_starts_none_and_one_that_is_not_ours_is_sent_nothing(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    here.daemon.up = False
    assert here("stop", "--daemon", "--scope", "demo") == 0
    assert here.said() == ["the SimMirror daemon is not running: nothing to stop"] and here.spawned == []
    other = Terminal(tmp_path / "other")
    other.daemon.admin = None
    assert other("stop", "--scope", "demo") == 1
    assert "is not your SimMirror daemon" in other.err.getvalue() and other.daemon.made("DELETE", DEMO) == []


def test_stop_daemon_stops_it_only_when_it_serves_no_other_scope_unless_forced(tmp_path: Path) -> None:
    here, signalled = stopping(tmp_path)
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": True, "shut_down": False}
    others = [
        {"id": "demo", "device": "iPhone 17", "agent": True, "building": False},
        {"id": "tp-2", "device": "iPhone 17 Pro", "agent": True, "building": True},
        {"id": "tp-3", "device": None, "agent": False, "building": False},
    ]
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": others}
    signal_daemon, marked = here.ctx.kill, []

    def kill(pid: int, sig: int) -> None:
        marked.append(stopped_mark(here.root / "run"))
        signal_daemon(pid, sig)

    here.ctx.kill = kill
    assert here("stop", "--daemon", "--scope", "demo") == 1
    assert stopped_mark(here.root / "run") is None, "still serving others, it is not being stopped"
    assert here.err.getvalue() == (
        "sim-mirror: the daemon still serves tp-2 (running iPhone 17 Pro, an agent, a build); tp-3, so it keeps "
        "running: stop each with `sim-mirror stop --scope <id>`, or add --force\n"
    )
    assert signalled == [] and here.daemon.up
    assert here("stop", "--daemon", "--force", "--scope", "demo") == 0
    assert signalled == [(os.getpid(), signal.SIGTERM)] and not here.daemon.up
    assert here.said()[-1] == "stopped the SimMirror daemon; the devices it did not shut down keep running"
    assert marked != [None] and marked == [stopped_mark(here.root / "run")], "written down before it was signalled"
    assert len(here.daemon.made("GET", "/api/v1/admin/in-use")) == 3, "forced, only who holds what is asked"
    alone, quit = stopping(tmp_path / "alone")
    alone.daemon.answers[("DELETE", DEMO)] = {"stopped": False}
    alone.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": others[:1]}
    assert alone("stop", "--daemon", "--scope", "demo") == 0 and quit == [(os.getpid(), signal.SIGTERM)]


def test_stop_daemon_says_so_when_it_cannot_be_stopped_by_its_pid_or_does_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = Terminal(tmp_path)
    here.daemon.answers[("DELETE", DEMO)] = {"stopped": False}
    here.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": []}
    assert here("stop", "--daemon", "--scope", "demo") == 1
    assert "wrote down no pid to stop it by" in here.err.getvalue()
    assert stopped_mark(here.root / "run") is None, "not stopped after all"
    stubborn, signalled = stopping(tmp_path / "stubborn")
    stubborn.ctx.kill = lambda pid, sig: signalled.append((pid, sig))
    stubborn.daemon.answers[("DELETE", DEMO)] = {"stopped": False}
    stubborn.daemon.answers[("GET", "/api/v1/admin/in-use")] = {"scopes": []}
    monkeypatch.setattr(stop_command, "wait_stopped", lambda client: False)
    assert stubborn("stop", "--daemon", "--scope", "demo") == 1 and signalled
    assert stubborn.err.getvalue() == "sim-mirror: the SimMirror daemon did not stop within 20s\n"


def test_a_stopping_daemon_is_waited_for_until_it_stops_answering_or_the_wait_runs_out(tmp_path: Path) -> None:
    here = Terminal(tmp_path)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    client = here.ctx.client()
    assert stop_command.wait_stopped(client, wait_s=1, poll_s=0.5, clock=lambda: now[0], sleep=sleep) is False
    assert now[0] == 1.0
    here.daemon.up = False
    assert stop_command.wait_stopped(client) is True
