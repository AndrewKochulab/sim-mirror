# SPDX-License-Identifier: Apache-2.0
"""The standalone daemon end to end, through its security middleware: every kind of token on every route, codes and
embed tickets, an agent's lease, a reload that stops what was switched off, and the screen socket's origin."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers

from sim_mirror.daemon import health
from sim_mirror.daemon.app import BAD_CODE, SERVER_SCOPE, Daemon, build_daemon, create_app
from sim_mirror.daemon.auth import (
    ADMIN_ONLY,
    AUTHENTICATION_REQUIRED,
    NAME_THE_SCOPE,
    NO_SUCH_SCOPE,
    NOT_AN_AGENT,
    NOT_FOR_SCOPE,
    TokenAuthenticator,
)
from sim_mirror.daemon.passes import ViewerSessions
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.protocol import CLOSE_FORBIDDEN, PROTOCOL_VERSION, SERVER
from sim_mirror.scope import Scope
from sim_mirror.seams import Refused
from sim_mirror.server.log_redaction import CredentialFilter
from sim_mirror.testing.asgi import HOST, AsgiSocket
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.rig import DeviceRig

OWN = "http://127.0.0.1:7466"
TP1 = Scope.named("tp-1")


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


@dataclass
class Site:
    rig: DeviceRig
    daemon: Daemon
    app: FastAPI

    @property
    def admin(self) -> dict[str, str]:
        return bearer(self.daemon.tokens.admin_token())

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=f"http://{HOST}")

    async def token(self, http: httpx.AsyncClient, kind: str, *scopes: str, **extra: Any) -> str:
        made = await http.post(
            "/api/v1/admin/tokens", headers=self.admin, json={"kind": kind, "scopes": list(scopes), **extra}
        )
        assert made.status_code == 200, made.text
        return str(made.json()["data"]["token"])

    async def started(self, http: httpx.AsyncClient, *, origin: str | None = None) -> str:
        headers = {**self.admin, **({"origin": origin} if origin else {})}
        answer = await http.post("/api/v1/scopes/tp-1", headers=headers)
        assert answer.status_code == 200, answer.text
        instance = self.daemon.runtime.manager.instance(TP1)
        assert instance is not None and instance.task is not None
        await instance.task
        return str(answer.json()["data"]["ticket"])


def site(tmp_path: Path) -> Site:
    rig = DeviceRig(tmp_path)
    daemon = build_daemon(
        config=rig.config,
        state=rig.state,
        tokens=TokenStore(tmp_path / "secrets"),
        port=7466,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        static_dir=None,
        clock=rig.clock,
        sleep=no_wait,
    )
    return Site(rig, daemon, create_app(daemon))


def detail(answer: httpx.Response) -> tuple[int, Any]:
    return answer.status_code, answer.json().get("detail")


async def test_the_daemon_is_up_on_its_port_and_answers_only_for_its_own_names(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        up = await http.get("/healthz")
        proven = await http.get("/healthz", params={"nonce": "n0nce"})
        too_long = await http.get("/healthz", params={"nonce": "x" * (health.NONCE_MAX + 1)})
        rebound = await http.get("/healthz", headers={"host": "rebound.example:7466"})
    assert up.json() == {"ok": True, "data": {"server": SERVER, "protocol": PROTOCOL_VERSION, "port": 7466}}
    assert proven.json()["data"]["proof"] == health.proof(here.daemon.tokens.admin_token(), "n0nce")
    assert too_long.status_code == 422
    assert rebound.status_code == 400
    here.rig.config.set(allowed_origins=("https://host.example",), frame_ancestors=("https://frame.example",))
    rules = here.daemon.site_rules()
    assert (rules.port, rules.allowed_origins, rules.frame_ancestors) == (
        7466,
        ("https://host.example",),
        ("https://frame.example",),
    )
    assert SERVER_SCOPE.id == "sim-mirror"


async def test_a_person_needs_a_token_for_the_scope_they_ask_about(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        viewer = await here.token(http, "viewer", "tp-1")
        agent = await here.token(http, "agent", "tp-1")
        assert detail(await http.get("/api/v1/scopes/tp-1")) == (401, AUTHENTICATION_REQUIRED)
        assert detail(await http.get("/api/v1/scopes/tp-1", headers=bearer("forged"))) == (401, AUTHENTICATION_REQUIRED)
        assert (await http.get("/api/v1/scopes/tp-1", headers=here.admin)).json()["ok"] is True
        assert (await http.get("/api/v1/scopes/tp-1", headers={"x-simmirror-token": viewer})).status_code == 200
        assert detail(await http.get("/api/v1/scopes/tp-2", headers=bearer(viewer))) == (403, NOT_FOR_SCOPE)
        assert detail(await http.get("/api/v1/scopes/tp-1", headers=bearer(agent))) == (403, NOT_FOR_SCOPE)
        assert detail(await http.get("/api/v1/scopes/bad%20id", headers=here.admin)) == (404, NO_SUCH_SCOPE)
        listed = (await http.get("/api/v1/admin/tokens", headers=here.admin)).json()["data"]["tokens"]
        viewer_id = next(entry["id"] for entry in listed if entry["kind"] == "viewer")
        revoked = await http.delete(f"/api/v1/admin/tokens/{viewer_id}", headers=here.admin)
        assert revoked.json() == {"ok": True, "data": {"revoked": True}}
        assert detail(await http.get("/api/v1/scopes/tp-1", headers=bearer(viewer))) == (401, AUTHENTICATION_REQUIRED)


async def test_only_the_admin_token_makes_lists_and_revokes_tokens(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        viewer = await here.token(http, "viewer", "tp-1", label="a page", roots=[])
        made = await http.post(
            "/api/v1/admin/tokens",
            headers=here.admin,
            json={"kind": "agent", "scopes": ["tp-1"], "roots": ["/Users/me/Notes"]},
        )
        record = made.json()["data"]
        assert record["kind"] == "agent" and record["roots"] == ["/Users/me/Notes"] and record["token"]
        refused = await http.post("/api/v1/admin/tokens", headers=here.admin, json={"kind": "root", "scopes": ["tp-1"]})
        assert detail(refused) == (400, "a token's kind is one of agent, viewer, admin")
        listed = (await http.get("/api/v1/admin/tokens", headers=here.admin)).json()["data"]["tokens"]
        assert [entry["kind"] for entry in listed] == ["viewer", "agent"]
        assert all("digest" not in entry and "token" not in entry for entry in listed)
        assert detail(await http.get("/api/v1/admin/tokens", headers=bearer(viewer))) == (403, ADMIN_ONLY)
        assert detail(await http.get("/api/v1/admin/tokens")) == (401, AUTHENTICATION_REQUIRED)
        assert detail(await http.get("/api/v1/admin/tokens", headers=bearer("forged"))) == (
            401,
            AUTHENTICATION_REQUIRED,
        )
        gone = await http.delete("/api/v1/admin/tokens/t-none", headers=here.admin)
        assert gone.json() == {"ok": True, "data": {"revoked": False}}
    assert here.daemon.tokens.roots("tp-1") == (Path("/Users/me/Notes"),)


async def test_a_login_code_opens_its_scopes_viewer_once(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        made = (await http.post("/api/v1/admin/login-codes", headers=here.admin, json={"scope": "tp-1"})).json()["data"]
        assert made["url"] == f"/viewer/tp-1#code={made['code']}" and made["expires_in_s"] == 60.0
        exchanged = (
            await http.post("/api/v1/auth/exchange", json={"code": made["code"]}, headers={"origin": OWN})
        ).json()
        session = exchanged["data"]["token"]
        assert exchanged["data"]["scope"] == "tp-1"
        assert (await http.get("/api/v1/scopes/tp-1", headers=bearer(session))).status_code == 200
        assert detail(await http.get("/api/v1/scopes/tp-2", headers=bearer(session))) == (403, NOT_FOR_SCOPE)
        assert detail(await http.get("/api/v1/admin/tokens", headers=bearer(session))) == (403, ADMIN_ONLY)
        assert detail(await http.post("/api/v1/auth/exchange", json={"code": made["code"]})) == (401, BAD_CODE)
        odd = await http.post("/api/v1/admin/login-codes", headers=here.admin, json={"scope": "bad id"})
        assert detail(odd) == (404, NO_SUCH_SCOPE)
        assert detail(await http.post("/api/v1/admin/login-codes", json={"scope": "tp-1"})) == (
            401,
            AUTHENTICATION_REQUIRED,
        )


async def test_a_hosts_backend_gets_an_embed_ticket_that_a_frame_spends_for_its_scope(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        viewer = await here.token(http, "viewer", "tp-1")
        ticket = (await http.post("/api/v1/scopes/tp-1/embed-tickets", headers=bearer(viewer))).json()["data"]
        code = ticket["url"].split("#ticket=", 1)[1]
        assert ticket["url"].startswith("/embed/tp-1#ticket=")
        spent = (await http.post("/api/v1/auth/exchange", json={"code": code})).json()["data"]
        assert spent["scope"] == "tp-1"
        refused = await http.post("/api/v1/scopes/tp-1/embed-tickets")
        assert detail(refused) == (401, AUTHENTICATION_REQUIRED)


async def test_an_agent_calls_tools_for_its_scope_and_holds_its_device_with_a_lease(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        agent = await here.token(http, "agent", "tp-1")
        several = await here.token(http, "agent", "tp-1", "tp-2")
        viewer = await here.token(http, "viewer", "tp-1")
        manifest = (await http.get("/api/v1/agent/manifest", headers=bearer(agent))).json()
        assert manifest["tools"][0]["name"] == "sim_device"
        called = (
            await http.post("/api/v1/agent/call", headers=bearer(agent), json={"name": "sim_device", "arguments": {}})
        ).json()
        assert called["content"][0]["text"] == "No simulator is running here; sim_device boot starts one."
        leased = (await http.post("/api/v1/agent/lease", headers=bearer(agent))).json()
        assert leased == {"ok": True, "data": {"scope": "tp-1", "expires_in_s": 90.0}}
        assert here.daemon.leases.held("tp-1")
        assert detail(await http.get("/api/v1/agent/manifest", headers=bearer(viewer))) == (403, NOT_AN_AGENT)
        assert detail(await http.get("/api/v1/agent/manifest", headers=bearer(several))) == (400, NAME_THE_SCOPE)
        named = {**bearer(several), "x-simmirror-scope": "tp-2"}
        assert (await http.get("/api/v1/agent/manifest", headers=named)).status_code == 200
        elsewhere = {**bearer(several), "x-simmirror-scope": "tp-9"}
        assert detail(await http.get("/api/v1/agent/manifest", headers=elsewhere)) == (403, NOT_FOR_SCOPE)
        assert detail(await http.post("/api/v1/agent/lease")) == (401, AUTHENTICATION_REQUIRED)
        assert detail(await http.get("/api/v1/agent/manifest", headers=bearer("forged"))) == (
            401,
            AUTHENTICATION_REQUIRED,
        )


@dataclass
class Asking:
    headers: Headers


async def test_an_agents_title_is_what_its_client_calls_itself_on_one_line(tmp_path: Path) -> None:
    tokens = TokenStore(tmp_path)
    everywhere = tokens.create("agent", ["*"])[1]
    auth = TokenAuthenticator(tokens, ViewerSessions())
    named = Headers(
        {"authorization": f"Bearer {everywhere}", "x-simmirror-scope": "demo", "x-simmirror-client": " Codex\n CLI "}
    )
    caller = await auth.agent(Asking(named))  # type: ignore[arg-type]
    assert (caller.scope.id, caller.title) == ("demo", "Codex CLI")
    untitled = Headers({"authorization": f"Bearer {everywhere}", "x-simmirror-scope": "demo"})
    assert (await auth.agent(Asking(untitled))).title == "agent"  # type: ignore[arg-type]
    with pytest.raises(Refused, match=NAME_THE_SCOPE):
        await auth.agent(Asking(Headers({"authorization": f"Bearer {everywhere}"})))  # type: ignore[arg-type]
    with pytest.raises(Refused, match=AUTHENTICATION_REQUIRED):
        await auth.agent(Asking(Headers({"authorization": "Bearer "})))  # type: ignore[arg-type]


async def test_a_reload_stops_what_the_settings_switched_off_before_it_answers(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        await here.started(http)
        here.rig.config.set(enabled=False)
        reloaded = await http.post("/api/v1/admin/reload", headers=here.admin)
        assert reloaded.json() == {"ok": True, "data": {"reloaded": True}}
        assert here.daemon.runtime.manager.instance(TP1) is None
        assert detail(await http.post("/api/v1/admin/reload")) == (401, AUTHENTICATION_REQUIRED)


async def test_a_page_opens_the_screen_with_its_ticket_from_its_own_origin_only(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        ticket = await here.started(http, origin=OWN)
    page = [("host", HOST), ("origin", OWN)]
    async with AsgiSocket(here.app, "/api/v1/scopes/tp-1/screen", query=f"ticket={ticket}", headers=page) as ws:
        assert await ws.accepted() and (await ws.text())["type"] == "hello"
    foreign = [("host", HOST), ("origin", "https://evil.example")]
    async with AsgiSocket(here.app, "/api/v1/scopes/tp-1/screen", query="ticket=x", headers=foreign) as refused:
        assert await refused.accepted() is False and refused.closed == (CLOSE_FORBIDDEN, "refused")
    async with AsgiSocket(here.app, "/api/v1/scopes/bad id/screen", query="ticket=x", headers=page) as odd:
        assert await odd.accepted() is False and odd.closed == (CLOSE_FORBIDDEN, NO_SUCH_SCOPE)


async def test_the_app_starts_and_closes_its_runtime_with_the_server(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.app.router.lifespan_context(here.app):
        assert here.daemon.runtime.reaper.running and here.rig.idb.reaped == 1
        await asyncio.sleep(0)
    assert not here.daemon.runtime.reaper.running
    assert any(isinstance(existing, CredentialFilter) for existing in logging.getLogger("uvicorn.access").filters)


async def test_the_app_says_it_has_stopped_once_its_runtime_is_closed(tmp_path: Path) -> None:
    here = site(tmp_path)
    stopped: list[bool] = []
    app = create_app(here.daemon, on_stopped=lambda: stopped.append(here.daemon.runtime.reaper.running))
    async with app.router.lifespan_context(app):
        assert stopped == []
    assert stopped == [False]


def test_a_daemon_built_without_a_sleep_sleeps_for_real(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    daemon = build_daemon(
        config=rig.config,
        state=rig.state,
        tokens=TokenStore(tmp_path),
        port=7481,
        registry=rig.registry,
        claims=rig.claims,
    )
    assert daemon.runtime.sleep is asyncio.sleep and daemon.port == 7481
