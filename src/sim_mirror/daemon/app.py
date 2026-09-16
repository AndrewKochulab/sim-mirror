# SPDX-License-Identifier: Apache-2.0
"""The standalone daemon's application: SimMirror's routers behind its security middleware, and the daemon's own.

    /healthz                                   whether it is up; given a nonce, proof it is this user's daemon
    /api/v1/scopes/{scope}[/devices|/device]   a person's routes (`server.http_routes`)
    /api/v1/scopes/{scope}/screen              the screen socket (`server.socket_routes`)
    /api/v1/scopes/{scope}/settings            the settings panel's (`server.settings_routes`), with a settings store
    /api/v1/scopes/{scope}/embed-tickets       a frame's way in, for a host's backend with its token
    /api/v1/agent/manifest|call|lease          an agent's routes, and its lease (`daemon.lease`)
    /api/v1/auth/exchange                      a code or an embed ticket, spent for a viewer token
    /api/v1/admin/reload|login-codes|tokens    the admin token's routes, and the settings changes waiting to be
    /api/v1/admin/settings-confirmations       confirmed, with their codes (`daemon.confirmations`)
    /api/v1/host[/tokens]                      a host token's own record, and the tokens it makes for its namespaces
    /viewer/{scope}, /embed/{scope}            the viewer's pages (`server.pages`)

`build_daemon` puts a daemon together from settings and a state folder; `create_app` serves it. The app starts the
runtime when it starts and closes it when it stops.

Several hosts can share one daemon, each with a host token for its namespaces (`daemon.tokens`): a scope in one host's
namespace never shares a device with a scope outside it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from sim_mirror._version import __version__
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.core.runtime import Runtime
from sim_mirror.daemon import health
from sim_mirror.daemon.auth import TokenAuthenticator
from sim_mirror.daemon.confirmations import PendingChanges
from sim_mirror.daemon.lease import LEASE_S, Leases
from sim_mirror.daemon.passes import CODE_TTL_S, OneShotCodes, ViewerSessions, ttl_of
from sim_mirror.daemon.policy import ConfigPolicy
from sim_mirror.daemon.tokens import TokenRecord, TokenRefused, TokenStore
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.protocol import PROTOCOL_VERSION, SERVER
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource, DeviceMemory, Refused, SettingsStore, StateStore
from sim_mirror.server import log_redaction
from sim_mirror.server.agent_routes import create_agent_router
from sim_mirror.server.envelope import ok
from sim_mirror.server.http_routes import create_http_router
from sim_mirror.server.pages import STATIC_VIEWER, create_page_router
from sim_mirror.server.security import SecurityMiddleware, SiteRules
from sim_mirror.server.settings_routes import create_settings_router
from sim_mirror.server.socket_routes import create_socket_router
from sim_mirror.storage.claims import Claims

SCOPES = "/api/v1/scopes/{scope_id}"
AGENT = "/api/v1/agent"
ADMIN = "/api/v1/admin"
HOST = "/api/v1/host"
#: The settings the server itself runs with -- its origins and framing -- are the file's own, not any scope's.
SERVER_SCOPE = Scope.named("sim-mirror")
BAD_CODE = "invalid or expired code"


class LoginCodeRequest(BaseModel):
    scope: str = Field(min_length=1, max_length=128)
    #: Open a session that may change settings, rather than only read them.
    settings: bool = False


class TokenRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    scopes: list[str] = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    roots: list[str] = Field(default_factory=list, max_length=32)


class HostTokenRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    scopes: list[str] = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    roots: list[str] = Field(default_factory=list, max_length=32)


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


@dataclass
class Daemon:
    runtime: Runtime
    config: ConfigSource
    tokens: TokenStore
    port: int
    leases: Leases
    codes: OneShotCodes = field(default_factory=OneShotCodes)
    viewers: ViewerSessions = field(default_factory=ViewerSessions)
    static_dir: Path | None = STATIC_VIEWER
    #: Where the settings panel reads and writes settings; without one, it is not served.
    settings: SettingsStore | None = None
    confirmations: PendingChanges = field(default_factory=PendingChanges)

    def site_rules(self) -> SiteRules:
        """Who the server answers to, from the settings as they are now."""
        config = self.config.get(SERVER_SCOPE)
        return SiteRules(self.port, config.allowed_origins, config.frame_ancestors)


def build_daemon(
    *,
    config: ConfigSource,
    state: StateStore,
    memory: DeviceMemory,
    tokens: TokenStore,
    port: int,
    copy: HostCopy | None = None,
    registry: ConnectorRegistry | None = None,
    claims: Claims | None = None,
    xcrun: XcrunRunner = run_xcrun,
    static_dir: Path | None = STATIC_VIEWER,
    settings: SettingsStore | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    platform: str = sys.platform,
) -> Daemon:
    leases = Leases(clock=clock)
    extra: dict[str, Any] = {} if sleep is None else {"sleep": sleep}

    def same_host(scope: Scope, other: Scope) -> bool:
        mine, theirs = tokens.host_of(scope.id), tokens.host_of(other.id)
        return (mine.id if mine else None) == (theirs.id if theirs else None)

    runtime = Runtime.build(
        config=config,
        state=state,
        memory=memory,
        policy=ConfigPolicy(config, tokens, state),
        copy=copy,
        usage=leases,
        registry=registry,
        claims=claims,
        xcrun=xcrun,
        may_share=same_host,
        clock=clock,
        platform=platform,
        **extra,
    )
    return Daemon(
        runtime=runtime,
        config=config,
        tokens=tokens,
        port=port,
        leases=leases,
        codes=OneShotCodes(clock=clock),
        viewers=ViewerSessions(clock=clock),
        static_dir=static_dir,
        settings=settings,
        confirmations=PendingChanges(clock=clock),
    )


def create_app(daemon: Daemon, on_stopped: Callable[[], object] | None = None) -> FastAPI:
    """The daemon's app. `on_stopped` runs once its runtime is closed -- still inside the server's shutdown, which a
    signal the server caught may end the process right after."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_redaction.install()
        await daemon.runtime.start()
        try:
            yield
        finally:
            await daemon.runtime.close()
            if on_stopped is not None:
                on_stopped()

    app = FastAPI(
        title="SimMirror", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(SecurityMiddleware, rules=daemon.site_rules)
    auth = TokenAuthenticator(daemon.tokens, daemon.viewers, lambda: daemon.site_rules().own_origins)

    def source() -> Runtime:
        return daemon.runtime

    app.include_router(create_http_router(source, auth), prefix=SCOPES)
    app.include_router(create_socket_router(source, auth), prefix=SCOPES)
    app.include_router(create_agent_router(source, auth), prefix=AGENT)
    if daemon.settings is not None:
        settings = create_settings_router(
            source, daemon.settings, auth, confirmations=daemon.confirmations, daemon_scope=SERVER_SCOPE
        )
        app.include_router(settings, prefix=SCOPES + "/settings")
    app.include_router(create_page_router(daemon.static_dir))

    def admin(request: Request) -> None:
        try:
            auth.admin(request)
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    def named(scope_id: str) -> Scope:
        try:
            return auth.scope(scope_id)
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    def host(request: Request) -> TokenRecord:
        try:
            return auth.host(request)
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    @app.get("/healthz")
    async def healthz(
        nonce: str | None = Query(None, max_length=health.NONCE_MAX),
        token_id: str | None = Query(None, max_length=32),
    ) -> dict[str, Any]:
        """Whether the daemon is up. Given a nonce, it proves it holds the admin token (`daemon.health`), so the CLI
        can tell it from anything else listening on its port before sending a credential -- and, given a scoped
        token's id too, that it knows that token, for a host holding no admin token."""
        proof = token_proof = None
        if nonce is not None:
            proof = health.proof(daemon.tokens.admin_token(), nonce)
            known = daemon.tokens.find(token_id) if token_id else None
            if known is not None:
                token_proof = health.token_proof(known.digest, nonce)
        return ok(
            {
                "server": SERVER,
                "protocol": PROTOCOL_VERSION,
                "port": daemon.port,
                "proof": proof,
                "token_proof": token_proof,
            }
        )

    @app.post(SCOPES + "/embed-tickets")
    async def embed_ticket(scope_id: str, request: Request) -> dict[str, Any]:
        """A one-shot ticket a host's backend puts in a frame's URL: ``/embed/<scope>#ticket=…``."""
        try:
            scope = (await auth.person(request, scope_id)).scope
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc
        ticket = daemon.codes.mint(scope.id, "embed")
        return ok({"url": f"/embed/{scope.id}#ticket={ticket}", "expires_in_s": CODE_TTL_S})

    @app.post("/api/v1/auth/exchange")
    async def exchange(body: ExchangeRequest) -> dict[str, Any]:
        """Spend a login code or an embed ticket for a viewer token for its scope, of the kind the code was for."""
        spent = daemon.codes.redeem(body.code)
        if spent is None:
            raise HTTPException(401, BAD_CODE)
        token = daemon.viewers.open(spent.scope_id, spent.kind)
        return ok({"token": token, "scope": spent.scope_id, "kind": spent.kind, "expires_in_s": ttl_of(spent.kind)})

    @app.post(AGENT + "/lease")
    async def agent_lease(request: Request) -> dict[str, Any]:
        """Keep this agent's scope's device from being reaped for another while."""
        try:
            caller = await auth.agent(request)
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc
        daemon.leases.renew(caller.scope.id, caller.key)
        return ok({"scope": caller.scope.id, "expires_in_s": LEASE_S})

    @app.post(ADMIN + "/reload")
    async def reload(request: Request) -> dict[str, Any]:
        """Act on changed settings now: what was switched off stops before this answers."""
        admin(request)
        await daemon.runtime.reconcile()
        return ok({"reloaded": True})

    @app.post(ADMIN + "/login-codes")
    async def login_code(body: LoginCodeRequest, request: Request) -> dict[str, Any]:
        """A one-shot code that opens a scope's viewer: ``/viewer/<scope>#code=…`` -- able to change its settings when
        asked for with ``settings``."""
        admin(request)
        scope = named(body.scope)
        code = daemon.codes.mint(scope.id, "settings" if body.settings else "viewer")
        return ok({"code": code, "url": f"/viewer/{scope.id}#code={code}", "expires_in_s": CODE_TTL_S})

    @app.post(ADMIN + "/settings-confirmations")
    async def settings_confirmations(request: Request) -> dict[str, Any]:
        """The sensitive settings changes waiting for a person to confirm them, each with its code. A POST, so no
        cache keeps a code."""
        admin(request)
        return ok({"pending": daemon.confirmations.waiting()})

    @app.post(ADMIN + "/tokens")
    async def create_token(body: TokenRequest, request: Request) -> dict[str, Any]:
        admin(request)
        try:
            record, token = daemon.tokens.create(body.kind, body.scopes, label=body.label, roots=body.roots)
        except TokenRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        return ok({**record.public(), "token": token})

    @app.get(ADMIN + "/tokens")
    async def list_tokens(request: Request) -> dict[str, Any]:
        admin(request)
        return ok({"tokens": [record.public() for record in daemon.tokens.records()]})

    @app.delete(ADMIN + "/tokens/{token_id}")
    async def revoke_token(token_id: str, request: Request) -> dict[str, Any]:
        """Revoke a token -- and, for a host token, every token that host made."""
        admin(request)
        return ok({"revoked": daemon.tokens.revoke(token_id)})

    @app.get(HOST)
    async def host_record(request: Request) -> dict[str, Any]:
        """The host token's own record: its namespaces and folders."""
        return ok(host(request).public())

    @app.post(HOST + "/tokens")
    async def host_create_token(body: HostTokenRequest, request: Request) -> dict[str, Any]:
        """An agent or viewer token for scopes in this host's namespaces, naming folders inside its own."""
        maker = host(request)
        try:
            record, token = daemon.tokens.create(body.kind, body.scopes, label=body.label, roots=body.roots, host=maker)
        except TokenRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        return ok({**record.public(), "token": token})

    @app.get(HOST + "/tokens")
    async def host_list_tokens(request: Request) -> dict[str, Any]:
        """The tokens this host made."""
        return ok({"tokens": [record.public() for record in daemon.tokens.made_by(host(request))]})

    @app.delete(HOST + "/tokens/{token_id}")
    async def host_revoke_token(token_id: str, request: Request) -> dict[str, Any]:
        """Revoke a token this host made; any other is not its to revoke, and is left as it is."""
        return ok({"revoked": daemon.tokens.revoke(token_id, host=host(request))})

    return app
