# SPDX-License-Identifier: Apache-2.0
"""What every command runs with: the environment and the state folder and config file it names, the streams, and every
way a command reaches outside -- the daemon, a new process, a server, a browser, Xcode -- each replaceable, so a test
runs a command with none of them."""

from __future__ import annotations

import asyncio
import copy
import os
import signal
import sys
import webbrowser
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import uvicorn
from fastapi import FastAPI

from sim_mirror.config.discovery import config_path
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.connectors.android.scrcpy_source import PINNED, ScrcpyJars, ScrcpyRelease
from sim_mirror.connectors.android.scrcpy_source import download as scrcpy_download
from sim_mirror.connectors.iphone.wda_source import Fetch, download
from sim_mirror.connectors.mcpbridge.client import BridgeClient
from sim_mirror.connectors.native.helper import helper_sources
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.daemon.app import SERVER_SCOPE
from sim_mirror.daemon.lifecycle import LOOPBACK, read_info, start_detached
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.doctor import checks
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.report import Report
from sim_mirror.mcp import relay
from sim_mirror.mcp.launcher import DaemonClient
from sim_mirror.platform import process
from sim_mirror.platform.adb import Adb
from sim_mirror.platform.android_sdk import AndroidSdk, Avd, avds, find_sdk
from sim_mirror.platform.java import Jdk, find_jdk
from sim_mirror.platform.process import Runner
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.scope import Scope
from sim_mirror.storage.app_support import AppSupportStateStore, state_dir

DAEMON_LOG = "daemon.log"
#: How long a stopping daemon waits on its open connections before it closes the runtime anyway. A connection to a page
#: the browser froze never drains, and uvicorn's own wait has no end.
SHUTDOWN_GRACE_S = 5

Serve = Callable[[FastAPI, str, int], Coroutine[Any, Any, None]]
Spawn = Callable[[Sequence[str], Path], Awaitable[Any]]


def daemon_log_config() -> dict[str, Any]:
    """uvicorn's logging, with SimMirror's own loggers written the same way: to stderr, which a detached daemon
    appends to its log."""
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["loggers"]["sim_mirror"] = {"handlers": ["default"], "level": "INFO"}
    return config


async def serve_with_uvicorn(app: FastAPI, host: str, port: int) -> None:
    """Serve until interrupted. Sockets use uvicorn's sans-I/O WebSockets, which let two sends wait on a full socket."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ws="websockets-sansio",
        log_level="info",
        log_config=daemon_log_config(),
        timeout_graceful_shutdown=SHUTDOWN_GRACE_S,
    )
    await uvicorn.Server(config).serve()


@dataclass
class CliContext:
    env: Mapping[str, str]
    cwd: Path
    stdout: IO[str]
    stderr: IO[str]
    stdin: IO[str]
    home: Path | None = None
    opener: relay.Opener | None = None
    open_url: Callable[[str], bool] = webbrowser.open
    spawn: Spawn = process.spawn
    #: How a process is signalled: ``os.kill``, or a test's.
    kill: Callable[[int, int], None] = os.kill
    serve: Serve = serve_with_uvicorn
    run: Runner = process.run
    xcrun: XcrunRunner = run_xcrun
    registry: Callable[[ConnectorContext], ConnectorRegistry] = ConnectorRegistry.discover
    diagnose: Callable[[DoctorContext], Awaitable[Report]] = checks.diagnose
    #: Xcode's tools, through mcpbridge, on the given Xcode.
    bridge: Callable[[str], BridgeClient] = BridgeClient
    #: Where this install keeps the native helper's Swift package, or None.
    helper_sources: Callable[[], Path | None] = helper_sources
    #: What fetches WebDriverAgent's release (`sim-mirror wda setup`).
    fetch: Fetch = download
    #: Fetches scrcpy's server, for `sim-mirror android setup`.
    scrcpy_fetch: Fetch = scrcpy_download
    #: The scrcpy release SimMirror pins.
    scrcpy_release: ScrcpyRelease = PINNED
    #: A client of the adb server this environment names.
    adb: Callable[[], Adb] = Adb
    #: Where the Mac's applications are, Android Studio's JDK among them.
    applications: Path = Path("/Applications")
    python: str = sys.executable

    @classmethod
    def from_process(cls) -> CliContext:
        return cls(env=dict(os.environ), cwd=Path.cwd(), stdout=sys.stdout, stderr=sys.stderr, stdin=sys.stdin)

    @property
    def state(self) -> AppSupportStateStore:
        return AppSupportStateStore(self.env, self.home)

    def config_path(self, explicit: str | None = None) -> Path:
        return Path(explicit).expanduser() if explicit else config_path(self.env, self.home)

    def config(self, explicit: str | None = None, overrides: Mapping[str, Any] | None = None) -> TomlConfigSource:
        return TomlConfigSource(self.config_path(explicit), env=self.env, overrides=overrides)

    def tokens(self) -> TokenStore:
        return TokenStore(self.state.state_dir)

    def scope(self, named: str | None) -> Scope:
        """The scope a command is for: the one named, else this folder's project."""
        return Scope.named(named) if named else Scope.for_folder(self.cwd)

    def daemon_url(self) -> str:
        info = read_info(self.state.run_dir())
        port = info.port if info is not None else self.config().get(SERVER_SCOPE).server_port
        return f"http://{LOOPBACK}:{port}"

    def client(self, url: str | None = None) -> DaemonClient:
        return DaemonClient(url or self.daemon_url(), self.tokens().admin_token(), opener=self.opener)

    def retire_daemon(self) -> bool:
        """Ask the daemon this user runs to stop, as ``Ctrl-C`` would: its devices keep running. Whether one was
        running to ask."""
        info = read_info(self.state.run_dir())
        if info is None:
            return False
        self.kill(info.pid, signal.SIGTERM)
        return True

    def start_daemon(self, *arguments: str) -> int:
        """Start ``sim-mirror serve`` detached, its output in the log folder. Answers its pid."""
        argv = [self.python, "-m", "sim_mirror", "serve", "--foreground", *arguments]
        return asyncio.run(start_detached(argv, self.state.log_dir() / DAEMON_LOG, spawn=self.spawn))

    def android_sdk(self, configured: str) -> AndroidSdk | None:
        """The Android SDK the settings name, else this environment's, else Android Studio's."""
        return find_sdk(configured, self.env, self.home)

    def avds(self, sdk: AndroidSdk | None) -> list[Avd]:
        return avds(sdk, self.env, self.home)

    def scrcpy_jars(self) -> ScrcpyJars:
        """scrcpy's server, kept under this environment's state folder."""
        return ScrcpyJars(
            root=lambda: state_dir(self.env, self.home) / "scrcpy", fetch=self.scrcpy_fetch, release=self.scrcpy_release
        )

    async def jdk(self, configured: str) -> Jdk | None:
        return await find_jdk(configured, self.env, self.home, runner=self.run, applications=self.applications)

    def doctor_android(self) -> dict[str, Any]:
        """What the doctor's Android checks reach, as this environment has it."""
        return {
            "android_sdk": self.android_sdk,
            "avds": self.avds,
            "adb": self.adb,
            "scrcpy": self.scrcpy_jars(),
            "jdk": self.jdk,
        }

    def say(self, text: str) -> None:
        self.stdout.write(text + "\n")

    def complain(self, text: str) -> None:
        self.stderr.write(text + "\n")
