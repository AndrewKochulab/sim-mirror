# SPDX-License-Identifier: Apache-2.0
"""The standalone daemon end to end, through its security middleware: every kind of token on every route, codes and
embed tickets, an agent's lease, a reload that stops what was switched off, the screen socket's origin -- and several
hosts on one daemon, each reaching its own scopes and devices only, through the routes and through `DaemonHost`."""

from __future__ import annotations

import asyncio
import io
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers

from sim_mirror.api import DaemonHost, DaemonRefused, DaemonUnavailable
from sim_mirror.config.settings_store import TomlSettingsStore
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.daemon import health
from sim_mirror.daemon.app import BAD_CODE, SERVER_SCOPE, Daemon, build_daemon, create_app
from sim_mirror.daemon.auth import (
    ADMIN_ONLY,
    AUTHENTICATION_REQUIRED,
    HOST_ONLY,
    NAME_THE_SCOPE,
    NO_SUCH_SCOPE,
    NOT_AN_AGENT,
    NOT_FOR_SCOPE,
    SETTINGS_NOT_IN_A_FRAME,
    SETTINGS_OWN_PAGES,
    TokenAuthenticator,
)
from sim_mirror.daemon.passes import ViewerSessions
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.mcp.launcher import SCOPE_ENV, TOKEN_ENV, URL_ENV
from sim_mirror.protocol import CLOSE_FORBIDDEN, PROTOCOL_VERSION, SERVER
from sim_mirror.scope import STANDALONE_GROUP, Scope
from sim_mirror.seams import Refused
from sim_mirror.server.log_redaction import CredentialFilter
from sim_mirror.testing.asgi import HOST, AsgiSocket
from sim_mirror.testing.fakes import BOOTED_UDID, no_wait
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


def site(tmp_path: Path, *, settings: bool = False) -> Site:
    rig = DeviceRig(tmp_path)
    source = TomlConfigSource(tmp_path / "config.toml", env={})
    daemon = build_daemon(
        config=source if settings else rig.config,
        state=rig.state,
        memory=rig.memory,
        tokens=TokenStore(tmp_path / "secrets"),
        port=7466,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        static_dir=None,
        settings=TomlSettingsStore(source) if settings else None,
        clock=rig.clock,
        sleep=no_wait,
    )
    return Site(rig, daemon, create_app(daemon))


async def session(site: Site, http: httpx.AsyncClient, scope_id: str = "tp-1", **extra: Any) -> str:
    made = await http.post("/api/v1/admin/login-codes", headers=site.admin, json={"scope": scope_id, **extra})
    spent = await http.post("/api/v1/auth/exchange", json={"code": made.json()["data"]["code"]})
    return str(spent.json()["data"]["token"])


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
        assert detail(refused) == (400, "a token's kind is one of agent, viewer, admin, host")
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
        assert (exchanged["data"]["scope"], exchanged["data"]["kind"]) == ("tp-1", "viewer")
        assert exchanged["data"]["expires_in_s"] == 12 * 3600.0
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
        assert (spent["scope"], spent["kind"]) == ("tp-1", "embed")
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
        memory=rig.memory,
        tokens=TokenStore(tmp_path),
        port=7481,
        registry=rig.registry,
        claims=rig.claims,
    )
    assert daemon.runtime.sleep is asyncio.sleep and daemon.port == 7481


# -- settings --------------------------------------------------------------------------------------------------------


async def test_settings_are_served_only_by_a_daemon_with_a_store(tmp_path: Path) -> None:
    without = site(tmp_path / "without")
    async with without.http() as http:
        assert (await http.get("/api/v1/scopes/tp-1/settings", headers=without.admin)).status_code == 404


async def test_who_may_read_and_change_settings_and_from_where(tmp_path: Path) -> None:
    here = site(tmp_path, settings=True)
    url = "/api/v1/scopes/tp-1/settings"
    fps = {"target": "scope", "set": [{"path": "stream.fps", "value": 12}], "unset": [], "confirmation": None}
    async with here.http() as http:
        settings = await session(here, http, settings=True)
        viewer = await session(here, http)
        ticket = (await http.post("/api/v1/scopes/tp-1/embed-tickets", headers=here.admin)).json()["data"]["url"]
        embed = (await http.post("/api/v1/auth/exchange", json={"code": ticket.split("=", 1)[1]})).json()["data"]
        agent = await here.token(http, "agent", "tp-1")
        viewer_token = await here.token(http, "viewer", "tp-1")
        own = {"origin": OWN, "sec-fetch-site": "same-origin"}
        # A settings session changes settings from the daemon's own page.
        changed = await http.patch(url, headers={**bearer(settings), **own}, json=fps)
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["access"] == "write" and "fps = 12" in (tmp_path / "config.toml").read_text()
        # A viewer session and a viewer token read them; neither changes them.
        for reader in (viewer, viewer_token):
            read = await http.get(url, headers={**bearer(reader), **own})
            assert read.status_code == 200 and read.json()["data"]["access"] == "read"
            assert (await http.patch(url, headers={**bearer(reader), **own}, json=fps)).status_code == 403
        # A framed viewer, an agent, another scope's session and no credential do not even read them.
        assert detail(await http.get(url, headers=bearer(embed["token"]))) == (403, SETTINGS_NOT_IN_A_FRAME)
        assert detail(await http.get(url, headers=bearer(agent))) == (403, NOT_FOR_SCOPE)
        other = await session(here, http, "tp-2", settings=True)
        assert detail(await http.get(url, headers=bearer(other))) == (403, NOT_FOR_SCOPE)
        assert detail(await http.get(url)) == (401, AUTHENTICATION_REQUIRED)
        assert detail(await http.get(url, headers=bearer("forged"))) == (401, AUTHENTICATION_REQUIRED)
        # The admin token from the command line changes everything; from a page, not the sensitive ones alone.
        assert (await http.get(url, headers=here.admin)).json()["data"]["access"] == "write_sensitive"
        assert (await http.get(url, headers={**here.admin, "origin": OWN})).json()["data"]["access"] == "write"


async def test_settings_refuse_every_other_origin_even_one_the_api_allows(tmp_path: Path) -> None:
    here = site(tmp_path, settings=True)
    listed_origin = "http://localhost:3000"
    (tmp_path / "config.toml").write_text(f'[security]\nallowed_origins = ["{listed_origin}"]\n')
    url = "/api/v1/scopes/tp-1/settings"
    async with here.http() as http:
        settings = await session(here, http, settings=True)
        listed = await http.get(url, headers={**bearer(settings), "origin": listed_origin})
        assert detail(listed) == (403, SETTINGS_OWN_PAGES)
        sneaky = await http.get(url, headers={**bearer(settings), "sec-fetch-site": "cross-site"})
        assert detail(sneaky) == (403, SETTINGS_OWN_PAGES)
        # The same session may still call a person's routes from that allowed origin: only settings keep to their own.
        status = await http.get("/api/v1/scopes/tp-1", headers={**bearer(settings), "origin": listed_origin})
        assert status.status_code == 200


async def test_a_sensitive_change_from_a_page_waits_for_the_code_the_terminal_shows(tmp_path: Path) -> None:
    here = site(tmp_path, settings=True)
    url = "/api/v1/scopes/tp-1/settings"
    tools = {"target": "scope", "set": [{"path": "build.tools", "value": True}], "unset": [], "confirmation": None}
    async with here.http() as http:
        settings = await session(here, http, settings=True)
        page = {**bearer(settings), "origin": OWN}
        held = await http.patch(url, headers=page, json=tools)
        assert held.status_code == 428 and held.json()["confirmation"]["command"] == "sim-mirror settings confirm"
        assert not (tmp_path / "config.toml").exists()
        confirming = await http.post("/api/v1/admin/settings-confirmations", headers=bearer(settings))
        assert detail(confirming) == (403, ADMIN_ONLY)
        waiting = (await http.post("/api/v1/admin/settings-confirmations", headers=here.admin)).json()["data"]
        (change,) = waiting["pending"]
        assert change["summary"] == "tp-1: build.tools = true" and change["scope"] == "tp-1"
        confirmed = await http.patch(url, headers=page, json={**tools, "confirmation": change["code"]})
        assert confirmed.status_code == 200 and "tools = true" in (tmp_path / "config.toml").read_text()
        empty = (await http.post("/api/v1/admin/settings-confirmations", headers=here.admin)).json()["data"]
        assert empty == {"pending": []}


# -- hosts sharing the daemon -------------------------------------------------------------------------------------

URL = "http://127.0.0.1:7466"


async def host_token(
    here: Site, http: httpx.AsyncClient, *scopes: str, roots: list[str] | None = None
) -> dict[str, Any]:
    made = await http.post(
        "/api/v1/admin/tokens",
        headers=here.admin,
        json={"kind": "host", "scopes": list(scopes), "roots": roots or [], "label": f"host of {scopes[0]}"},
    )
    assert made.status_code == 200, made.text
    return dict(made.json()["data"])


async def up(here: Site, http: httpx.AsyncClient, token: str, scope_id: str) -> str:
    answer = await http.post(f"/api/v1/scopes/{scope_id}", headers=bearer(token))
    assert answer.status_code == 200, answer.text
    instance = here.daemon.runtime.manager.instance(Scope.named(scope_id))
    assert instance is not None and instance.task is not None
    await instance.task
    return instance.udid


async def test_a_host_is_made_by_the_admin_for_namespaces_nobody_else_has(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        notes = await host_token(here, http, "notes:*", roots=[str(tmp_path)])
        assert (notes["kind"], notes["scopes"], notes["host"]) == ("host", ["notes:*"], "")
        taken = await http.post(
            "/api/v1/admin/tokens", headers=here.admin, json={"kind": "host", "scopes": ["notes:*"]}
        )
        assert detail(taken) == (400, "another host already has the namespace notes")
        exact = await http.post("/api/v1/admin/tokens", headers=here.admin, json={"kind": "host", "scopes": ["tp-1"]})
        assert detail(exact) == (400, "a host token is for namespaces only, such as notes:*")
        record = await http.get("/api/v1/host", headers=bearer(notes["token"]))
        assert record.json()["data"] == {key: value for key, value in notes.items() if key != "token"}
        assert detail(await http.get("/api/v1/host", headers=here.admin)) == (403, HOST_ONLY)
        assert detail(await http.get("/api/v1/host")) == (401, AUTHENTICATION_REQUIRED)
        assert detail(await http.get("/api/v1/host", headers=bearer("forged"))) == (401, AUTHENTICATION_REQUIRED)


async def test_a_host_reaches_its_own_scopes_and_none_of_the_rest(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        notes = (await host_token(here, http, "notes:*"))["token"]
        await host_token(here, http, "mail:*")
        assert (await http.get("/api/v1/scopes/notes:42", headers=bearer(notes))).status_code == 200
        assert (await http.post("/api/v1/scopes/notes:42/embed-tickets", headers=bearer(notes))).status_code == 200
        for other in ("mail:1", "tp-1", "notes"):
            assert detail(await http.get(f"/api/v1/scopes/{other}", headers=bearer(notes))) == (403, NOT_FOR_SCOPE)
        assert detail(await http.get("/api/v1/admin/tokens", headers=bearer(notes))) == (403, ADMIN_ONLY)
        assert detail(await http.get("/api/v1/agent/manifest", headers=bearer(notes))) == (403, NOT_AN_AGENT)


async def test_a_hosts_scopes_are_its_own_group_and_the_rest_are_the_macs(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        notes = (await host_token(here, http, "notes:*"))["token"]
        await up(here, http, notes, "notes:42")
        await up(here, http, here.daemon.tokens.admin_token(), "tp-1")
    instances = {instance.owner.id: instance.owner for instance in here.daemon.runtime.manager.instances()}
    assert instances["notes:42"].group == "notes" and instances["tp-1"].group == STANDALONE_GROUP


async def test_a_host_makes_lists_and_revokes_its_own_tokens_and_revoking_the_host_revokes_them(tmp_path: Path) -> None:
    here = site(tmp_path)
    (tmp_path / "Projects" / "App").mkdir(parents=True)
    async with here.http() as http:
        notes = await host_token(here, http, "notes:*", roots=[str(tmp_path / "Projects")])
        mail = (await host_token(here, http, "mail:*"))["token"]
        auth = bearer(notes["token"])
        made = await http.post(
            "/api/v1/host/tokens",
            headers=auth,
            json={"kind": "agent", "scopes": ["notes:42"], "roots": [str(tmp_path / "Projects" / "App")]},
        )
        agent = made.json()["data"]
        assert (agent["kind"], agent["host"]) == ("agent", notes["id"]) and agent["token"]
        outside = await http.post(
            "/api/v1/host/tokens", headers=auth, json={"kind": "agent", "scopes": ["mail:1"], "roots": []}
        )
        assert detail(outside) == (400, "mail:1 is not in this host's namespaces: notes:*")
        admin = await http.post("/api/v1/host/tokens", headers=auth, json={"kind": "admin", "scopes": ["notes:1"]})
        assert detail(admin) == (400, "a host makes agent and viewer tokens only")
        viewer = (
            await http.post("/api/v1/host/tokens", headers=bearer(mail), json={"kind": "viewer", "scopes": ["mail:1"]})
        ).json()["data"]
        listed = (await http.get("/api/v1/host/tokens", headers=auth)).json()["data"]["tokens"]
        assert [entry["id"] for entry in listed] == [agent["id"]] and "token" not in listed[0]
        theirs = await http.delete(f"/api/v1/host/tokens/{viewer['id']}", headers=auth)
        assert theirs.json()["data"] == {"revoked": False}
        manifest = await http.get("/api/v1/agent/manifest", headers=bearer(agent["token"]))
        assert manifest.status_code == 200
        assert detail(
            await http.post("/api/v1/host/tokens", headers=here.admin, json={"kind": "viewer", "scopes": ["x"]})
        ) == (
            403,
            HOST_ONLY,
        )
        gone = await http.delete(f"/api/v1/admin/tokens/{notes['id']}", headers=here.admin)
        assert gone.json()["data"] == {"revoked": True}
        refused = await http.get("/api/v1/agent/manifest", headers=bearer(agent["token"]))
        assert detail(refused) == (401, AUTHENTICATION_REQUIRED)
        mine = await http.delete(f"/api/v1/host/tokens/{viewer['id']}", headers=bearer(mail))
        assert mine.json()["data"] == {"revoked": True}


async def test_a_device_a_scope_of_one_host_runs_is_neither_listed_for_nor_given_to_another(tmp_path: Path) -> None:
    here = site(tmp_path)
    manager = here.daemon.runtime.manager
    async with here.http() as http:
        notes = (await host_token(here, http, "notes:*"))["token"]
        mail = (await host_token(here, http, "mail:*"))["token"]
        admin = here.daemon.tokens.admin_token()
        # mail:1 is set on the booted device before anybody runs it; then notes:42 runs that device.
        assert (
            await http.put("/api/v1/scopes/mail:1/device", headers=bearer(mail), json={"udid": BOOTED_UDID})
        ).status_code == 200
        assert (
            await http.put("/api/v1/scopes/notes:42/device", headers=bearer(notes), json={"udid": BOOTED_UDID})
        ).status_code == 200
        assert await up(here, http, notes, "notes:42") == BOOTED_UDID
        listed = (await http.get("/api/v1/scopes/mail:2/devices", headers=bearer(mail))).json()["data"]["devices"]
        assert BOOTED_UDID not in [device["udid"] for device in listed] and listed
        ours = (await http.get("/api/v1/scopes/notes:7/devices", headers=bearer(notes))).json()["data"]["devices"]
        assert BOOTED_UDID in [device["udid"] for device in ours]
        said = here.rig.copy.device_in_use_elsewhere()
        for token, scope_id in ((mail, "mail:2"), (admin, "tp-1")):
            picked = await http.put(
                f"/api/v1/scopes/{scope_id}/device", headers=bearer(token), json={"udid": BOOTED_UDID}
            )
            assert detail(picked) == (409, said)
        assert detail(await http.post("/api/v1/scopes/mail:1", headers=bearer(mail))) == (409, said)
        # Another scope of the same host joins it.
        assert (
            await http.put("/api/v1/scopes/notes:7/device", headers=bearer(notes), json={"udid": BOOTED_UDID})
        ).status_code == 200
        joined = await http.post("/api/v1/scopes/notes:7", headers=bearer(notes))
        assert joined.status_code == 200
        instance = manager.instance(Scope.named("notes:7"))
        assert instance is not None and instance.scopes == {"notes:42", "notes:7"}
        # Once it is let go, the other host may have it.
        await http.delete("/api/v1/scopes/notes:42", headers=bearer(notes))
        await http.delete("/api/v1/scopes/notes:7", headers=bearer(notes))
        assert await up(here, http, mail, "mail:1") == BOOTED_UDID


async def test_a_host_changes_its_own_scopes_settings_one_at_a_time_and_confirms_sensitive_ones(tmp_path: Path) -> None:
    here = site(tmp_path, settings=True)
    async with here.http() as http:
        notes = (await host_token(here, http, "notes:*"))["token"]
        path = "/api/v1/scopes/notes:42/settings"
        read = await http.get(path, headers=bearer(notes))
        assert read.status_code == 200
        everywhere = await http.patch(
            path, headers=bearer(notes), json={"target": "all", "set": [{"path": "stream.fps", "value": 20}]}
        )
        assert detail(everywhere) == (403, here.rig.copy.settings_this_scope_only())
        mine = await http.patch(
            path, headers=bearer(notes), json={"target": "scope", "set": [{"path": "stream.fps", "value": 20}]}
        )
        assert mine.status_code == 200, mine.text
        assert here.daemon.config.get(Scope("notes:42", "notes", "notes:42")).stream_fps == 20
        assert here.daemon.config.get(Scope.named("notes:43")).stream_fps == 30
        sensitive = await http.patch(
            path, headers=bearer(notes), json={"target": "scope", "set": [{"path": "build.tools", "value": True}]}
        )
        assert sensitive.status_code == 428
        other = await http.get("/api/v1/scopes/mail:1/settings", headers=bearer(notes))
        assert detail(other) == (403, NOT_FOR_SCOPE)


async def test_the_daemon_proves_it_knows_a_scoped_token_to_its_holder(tmp_path: Path) -> None:
    here = site(tmp_path)
    async with here.http() as http:
        notes = await host_token(here, http, "notes:*")
        answered = (await http.get("/healthz", params={"nonce": "n0nce", "token_id": notes["id"]})).json()["data"]
        unknown = (await http.get("/healthz", params={"nonce": "n0nce", "token_id": "t-none"})).json()["data"]
    assert health.token_proves(notes["token"], "n0nce", answered["token_proof"])
    assert not health.token_proves("another token", "n0nce", answered["token_proof"])
    assert "token_proof" not in unknown and "proof" in unknown


# -- DaemonHost --------------------------------------------------------------------------------------------------


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def asgi_opener(
    http: httpx.AsyncClient, loop: asyncio.AbstractEventLoop
) -> Callable[[urllib.request.Request, float], Response]:
    """An opener a host's thread uses, answered by the daemon's app on the test's event loop."""

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        path = request.full_url.removeprefix(URL)
        sent = http.request(request.get_method(), path, content=request.data, headers=dict(request.header_items()))
        answer = asyncio.run_coroutine_threadsafe(sent, loop).result(timeout)
        if answer.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url, answer.status_code, "refused", None, io.BytesIO(answer.content)
            )  # type: ignore[arg-type]
        return Response(answer.content)

    return opener


async def threaded(call: Callable[[], Any]) -> Any:
    return await asyncio.to_thread(call)


async def test_a_host_backend_does_everything_through_daemon_host(tmp_path: Path) -> None:
    here = site(tmp_path, settings=True)
    (tmp_path / "Projects").mkdir()
    async with here.http() as http:
        notes = await host_token(here, http, "notes:*", roots=[str(tmp_path / "Projects")])
        host = DaemonHost(URL, notes["token"], notes["id"], opener=asgi_opener(http, asyncio.get_running_loop()))
        assert (await threaded(host.record))["scopes"] == ["notes:*"]
        started = await threaded(lambda: host.start("notes:42"))
        assert started["ticket"] and (await threaded(lambda: host.status("notes:42")))["enabled"] is True
        instance = here.daemon.runtime.manager.instance(Scope.named("notes:42"))
        assert instance is not None and instance.task is not None
        await instance.task
        devices = await threaded(lambda: host.devices("notes:42"))
        assert devices and await threaded(lambda: host.choose("notes:43", devices[0]["udid"])) is None
        url = await threaded(lambda: host.embed_url("notes:42"))
        assert url.startswith(f"{URL}/embed/notes:42#ticket=")
        assert (await threaded(lambda: host.settings("notes:42")))["access"]
        changed = await threaded(lambda: host.change_settings("notes:42", {"stream.fps": 24}, unset=["agent.cursor"]))
        assert changed["access"]
        access = await threaded(lambda: host.agent("notes:42", label="Notes agent", roots=[str(tmp_path / "Projects")]))
        assert access.env[URL_ENV] == f"{URL}/api/v1/agent" and access.env[SCOPE_ENV] == "notes:42"
        assert access.env[TOKEN_ENV] not in " ".join(access.argv) and "--url-env" in access.argv
        other = await threaded(lambda: host.agent("notes:42", python="/usr/bin/python3", server_name="notes-sim"))
        assert other.argv[0] == "/usr/bin/python3" and "notes-sim" in other.argv
        assert await threaded(lambda: host.keep(access)) is True
        assert [entry["id"] for entry in await threaded(host.tokens)] == [access.token_id, other.token_id]
        assert await threaded(lambda: host.revoke(access.token_id)) is True
        assert await threaded(lambda: host.keep(access)) is False
        with pytest.raises(DaemonRefused) as refused:
            await threaded(lambda: host.status("mail:1"))
        assert (refused.value.status, str(refused.value)) == (403, NOT_FOR_SCOPE)
        assert await threaded(lambda: host.stop("notes:42", shutdown=True)) is True
        assert await threaded(lambda: host.stop("notes:42")) is False


def test_a_host_sends_nothing_to_a_listener_that_does_not_prove_it_knows_the_token() -> None:
    sent: list[str] = []

    def impostor(request: urllib.request.Request, timeout: float) -> Response:
        sent.append(request.full_url)
        return Response(b'{"ok": true, "data": {"token_proof": "made up"}}')

    host = DaemonHost(URL, "secret", "t1", opener=impostor)
    with pytest.raises(DaemonUnavailable, match="did not prove it knows this host token, so nothing was sent to it"):
        host.record()
    assert len(sent) == 1 and "token_id=t1" in sent[0] and "secret" not in sent[0]

    def nothing(request: urllib.request.Request, timeout: float) -> Response:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(DaemonUnavailable, match="could not be reached"):
        DaemonHost(URL, "secret", "t1", opener=nothing).record()
    with pytest.raises(ValueError, match="on this Mac only"):
        DaemonHost("https://example.com", "secret", "t1")
