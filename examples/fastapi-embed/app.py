# SPDX-License-Identifier: Apache-2.0
"""A Python host application embedding SimMirror: its own FastAPI app, its own sign-in, SimMirror's routers under its
own paths.

    EXAMPLE_KEY=change-me uv run uvicorn --factory app:create_app --port 7484

Everything a host decides is a seam (`sim_mirror.api`). Here settings are SimMirror's embedded defaults with the
simulator switched on; state lives under ``~/.sim-mirror-example``; every project may have a simulator but none may run
commands; and a request is let in by a shared key in ``X-Example-Key``. A real host checks its own session there.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.websockets import WebSocket

from sim_mirror.api import (
    Admission,
    Caller,
    JsonDeviceMemory,
    Person,
    Refused,
    Runtime,
    Scope,
    SimConfig,
    claims_dir,
    create_agent_router,
    create_http_router,
    create_socket_router,
)

KEY_ENV = "EXAMPLE_KEY"
KEY_HEADER = "x-example-key"
PROJECT_HEADER = "x-example-project"
CLIENT_HEADER = "x-example-client"
GROUP = "example"
#: Where a person's viewer reaches a project's simulator, and where agents call tools.
PROJECTS = "/api/projects/{scope_id}/simulator"
AGENT = "/api/agent/simulator"
STATE_ROOT = Path.home() / ".sim-mirror-example"
#: Device claims are shared by every host on the Mac, so two hosts never drive one device. Asking SimMirror where
#: they go, rather than writing the path out, is what makes a standalone daemon and this example see each other's.
SHARED_CLAIMS = claims_dir(os.environ)


def project(scope_id: str) -> Scope:
    """A project's scope, or a refusal for an id that cannot be one."""
    try:
        return Scope(id=scope_id, group=GROUP, label=scope_id)
    except ValueError as exc:
        raise Refused(404, "there is no such project") from exc


class ExampleConfig:
    """SimMirror's embedded defaults -- the simulator off, build tools on -- with the simulator switched on."""

    def __init__(self) -> None:
        self.config = SimConfig.defaults("embedded").with_values(enabled=True)

    def get(self, scope: Scope) -> SimConfig:
        return self.config


class ExampleState:
    """Every file under one folder of this application's, except the claims all hosts share."""

    def __init__(self, root: Path = STATE_ROOT, claims: Path = SHARED_CLAIMS) -> None:
        self.root = root
        self.claims = claims

    @property
    def owner_tag(self) -> str:
        return "SimMirrorExample"

    def builds_dir(self, scope: Scope) -> Path:
        return self.root / "builds" / scope.id

    def derived_data(self, scope: Scope) -> Path:
        return self.root / "DerivedData" / scope.id

    def run_dir(self) -> Path:
        return self.root / "run"

    def log_dir(self) -> Path:
        return self.root / "logs"

    def claims_dir(self) -> Path:
        return self.claims

    def ensure_dir(self, folder: Path) -> Path:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder


class ExamplePolicy:
    """Every project may have a simulator; none may run commands, so builds and tests are refused."""

    def area_enabled(self, scope: Scope) -> bool:
        return True

    def shells_allowed(self, scope: Scope) -> bool:
        return False

    def install_roots(self, scope: Scope) -> tuple[Path, ...]:
        return ()

    def build_folder(self, scope: Scope) -> Path | None:
        return None


class SharedKey:
    """Lets in what carries the application's key. A real host checks its own signed-in session here instead."""

    def __init__(self, key: str) -> None:
        self.key = key.encode("utf-8")

    def _check(self, headers: Mapping[str, str]) -> None:
        given = headers.get(KEY_HEADER, "").encode("utf-8")
        if not self.key or not hmac.compare_digest(given, self.key):
            raise Refused(401, "authentication required")

    async def person(self, request: Request, scope_id: str) -> Person:
        self._check(request.headers)
        return Person(project(scope_id))

    async def agent(self, request: Request) -> Caller:
        self._check(request.headers)
        scope = project(request.headers.get(PROJECT_HEADER, ""))
        title = " ".join(request.headers.get(CLIENT_HEADER, "").split())[:80] or "agent"
        return Caller(scope=scope, key=f"{scope.id}:{title}", title=title)

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> Admission:
        # A browser cannot put headers on a WebSocket. What lets a screen socket in is its one-shot ticket, which
        # SimMirror checks next, and which only a person let in by `person` could have been given.
        return Admission(project(scope_id))


def build_runtime(state: ExampleState | None = None) -> Runtime:
    # `memory` says where a scope's device is remembered. This example takes the one SimMirror ships, over a file of
    # its own; a host with a database puts it there instead by passing its own `DeviceMemory`, and then implements
    # nothing about files at all.
    state = state or ExampleState()
    return Runtime.build(
        config=ExampleConfig(),
        state=state,
        policy=ExamplePolicy(),
        memory=JsonDeviceMemory(state.root / "devices.json"),
    )


def create_app(runtime: Runtime | None = None, *, key: str | None = None) -> FastAPI:
    """The application. SimMirror starts with it -- ending what an earlier run left -- and stops with it."""
    simulators = runtime or build_runtime()
    auth = SharedKey(os.environ.get(KEY_ENV, "") if key is None else key)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await simulators.start()
        try:
            yield
        finally:
            await simulators.close()

    app = FastAPI(title="SimMirror embedded in an application", lifespan=lifespan)

    def source() -> Runtime:
        return simulators

    app.include_router(create_http_router(source, auth), prefix=PROJECTS)
    app.include_router(create_socket_router(source, auth), prefix=PROJECTS)
    app.include_router(create_agent_router(source, auth), prefix=AGENT)
    return app
