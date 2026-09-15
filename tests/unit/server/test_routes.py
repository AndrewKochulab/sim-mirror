# SPDX-License-Identifier: Apache-2.0
"""The router factories a host mounts: a person's status, start, stop and device routes; the screen socket's order of
admission; and an agent's manifest and calls -- each asking the host who is there first."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, WebSocket

from sim_mirror.core.runtime import Runtime
from sim_mirror.protocol import CLOSE_BAD_GATEWAY, CLOSE_FORBIDDEN, CLOSE_UNAUTHORIZED
from sim_mirror.scope import Scope
from sim_mirror.seams import Admission, Caller, Person, Refused
from sim_mirror.server.agent_routes import BODY_MAX, create_agent_router
from sim_mirror.server.envelope import NOT_RUNNING
from sim_mirror.server.http_routes import create_http_router
from sim_mirror.server.socket_routes import BAD_TICKET, create_socket_router
from sim_mirror.testing.asgi import HOST, AsgiSocket
from sim_mirror.testing.fakes import BOOTED_UDID, no_wait
from sim_mirror.testing.rig import DeviceRig, scope

PREFIX = "/api/v1/scopes/{scope_id}"
OWN = "http://127.0.0.1:7466"


class Door:
    """A host's authenticator: lets everyone in as the scope they name, until a test sets a refusal."""

    def __init__(self, *, accepts: bool = False) -> None:
        self.refusal: Refused | None = None
        self.accepts = accepts

    def _check(self) -> None:
        if self.refusal is not None:
            raise self.refusal

    async def person(self, request: Request, scope_id: str) -> Person:
        self._check()
        return Person(scope(scope_id))

    async def agent(self, request: Request) -> Caller:
        self._check()
        return Caller(scope(request.headers.get("x-scope", "tp-1")), key="agent-1", title="Codex")

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> Admission:
        self._check()
        if self.accepts:
            await websocket.accept()
        return Admission(scope(scope_id))


@dataclass
class Served:
    rig: DeviceRig
    runtime: Runtime
    door: Door
    app: FastAPI = field(default_factory=FastAPI)
    running: bool = True

    def __post_init__(self) -> None:
        def source() -> Runtime | None:
            return self.runtime if self.running else None

        self.app.include_router(create_http_router(source, self.door), prefix=PREFIX)
        self.app.include_router(create_socket_router(source, self.door), prefix=PREFIX)
        self.app.include_router(create_agent_router(source, self.door), prefix="/api/v1/agent")

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=f"http://{HOST}")

    def screen(self, ticket: str, *, origin: str | None = None, scope_id: str = "tp-1") -> AsgiSocket:
        headers = [("host", HOST), *([("origin", origin)] if origin else [])]
        return AsgiSocket(self.app, f"/api/v1/scopes/{scope_id}/screen", query=f"ticket={ticket}", headers=headers)

    async def started(self, *, origin: str | None = None) -> str:
        async with self.http() as http:
            answer = await http.post("/api/v1/scopes/tp-1", headers={"origin": origin} if origin else {})
        assert answer.status_code == 200, answer.text
        instance = self.runtime.manager.instance(scope())
        assert instance is not None and instance.task is not None
        await instance.task
        return str(answer.json()["data"]["ticket"])


def served(tmp_path: Path, door: Door | None = None) -> Served:
    rig = DeviceRig(tmp_path)
    runtime = Runtime.build(
        config=rig.config,
        state=rig.state,
        policy=rig.policy,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        clock=rig.clock,
        sleep=no_wait,
    )
    return Served(rig, runtime, door or Door())


# -- a person's routes -----------------------------------------------------------------------------------------------


async def test_a_person_sees_the_scopes_status_starts_its_device_and_stops_it(tmp_path: Path) -> None:
    site = served(tmp_path)
    async with site.http() as http:
        status = (await http.get("/api/v1/scopes/tp-1")).json()
        assert status["ok"] is True and status["data"]["enabled"] is True and status["data"]["device"] is None
        assert status["data"]["connector"] == "idb"
        started = (await http.post("/api/v1/scopes/tp-1")).json()["data"]
        assert started["ticket"] and started["device"]["state"] in ("booting", "ready")
        instance = site.runtime.manager.instance(scope())
        assert instance is not None and instance.task is not None
        await instance.task
        stopped = await http.delete("/api/v1/scopes/tp-1", params={"shutdown": "true"})
    assert stopped.json() == {"ok": True, "data": {"stopped": True}}
    assert ("simctl", "shutdown", instance.udid) in site.rig.argv()


async def test_a_person_picks_a_device_from_this_macs_simulators(tmp_path: Path) -> None:
    site = served(tmp_path)
    async with site.http() as http:
        devices = (await http.get("/api/v1/scopes/tp-1/devices")).json()["data"]["devices"]
        assert BOOTED_UDID in [device["udid"] for device in devices]
        chosen = await http.put("/api/v1/scopes/tp-1/device", json={"udid": BOOTED_UDID})
        assert chosen.json() == {"ok": True, "data": {"udid": BOOTED_UDID}}
        assert (await http.put("/api/v1/scopes/tp-1/device", json={"udid": ""})).status_code == 422


async def test_a_scope_that_cannot_have_a_simulator_is_refused_with_why(tmp_path: Path) -> None:
    site = served(tmp_path)
    site.rig.config.set(enabled=False)
    off = {"detail": "The iOS Simulator is off for this project (`sim-mirror config`)."}
    async with site.http() as http:
        for answer in (
            await http.post("/api/v1/scopes/tp-1"),
            await http.get("/api/v1/scopes/tp-1/devices"),
            await http.put("/api/v1/scopes/tp-1/device", json={"udid": BOOTED_UDID}),
        ):
            assert (answer.status_code, answer.json()) == (409, off)
        status = (await http.get("/api/v1/scopes/tp-1")).json()["data"]
    assert status["enabled"] is False and status["reason"] == off["detail"]


async def test_a_person_the_host_refuses_or_a_server_without_a_runtime_is_answered_so(tmp_path: Path) -> None:
    site = served(tmp_path)
    site.door.refusal = Refused(404, "there is no such project")
    async with site.http() as http:
        refused = await http.get("/api/v1/scopes/nope")
        assert (refused.status_code, refused.json()) == (404, {"detail": "there is no such project"})
        site.door.refusal = None
        site.running = False
        idle = await http.get("/api/v1/scopes/tp-1")
        assert (idle.status_code, idle.json()) == (503, {"detail": NOT_RUNNING})
        assert (await http.delete("/api/v1/scopes/tp-1")).json() == {"ok": True, "data": {"stopped": False}}


# -- the screen socket -----------------------------------------------------------------------------------------------


async def test_a_ticket_opens_the_scopes_screen_once_and_the_relay_says_hello(tmp_path: Path) -> None:
    site = served(tmp_path)
    ticket = await site.started()
    async with site.screen(ticket) as ws:
        assert await ws.accepted()
        hello = await ws.text()
        assert hello["type"] == "hello" and hello["connector"] == "idb"
        await ws.say({"type": "hello", "v": 1, "encodings": ["jpeg"]})
        assert await ws.text() == {"type": "stream", "encoding": "jpeg"}
        status = await ws.text()
        assert status["type"] == "status" and status["state"] == "ready"
    async with site.screen(ticket) as again:
        assert await again.accepted() and await again.text() is None
        assert again.closed == (CLOSE_UNAUTHORIZED, BAD_TICKET)


async def test_a_ticket_minted_for_a_page_opens_the_screen_only_from_that_page(tmp_path: Path) -> None:
    site = served(tmp_path)
    ticket = await site.started(origin=OWN)
    async with site.screen(ticket, origin="http://localhost:7466") as elsewhere:
        assert await elsewhere.accepted() and await elsewhere.text() is None
        assert elsewhere.closed == (CLOSE_UNAUTHORIZED, BAD_TICKET)
    ticket = await site.started(origin=OWN)
    async with site.screen(ticket, origin=OWN) as page:
        assert await page.accepted() and (await page.text())["type"] == "hello"


async def test_a_socket_the_host_refuses_is_closed_before_it_is_accepted(tmp_path: Path) -> None:
    site = served(tmp_path)
    site.door.refusal = Refused(CLOSE_FORBIDDEN, "refused")
    async with site.screen("any") as ws:
        assert await ws.accepted() is False and ws.closed == (CLOSE_FORBIDDEN, "refused")


@pytest.mark.parametrize("accepts", [False, True])
async def test_a_socket_is_accepted_once_whoever_accepts_it_and_then_told_why_it_cannot_stay(
    tmp_path: Path, accepts: bool
) -> None:
    site = served(tmp_path, Door(accepts=accepts))
    async with site.screen("forged") as ws:
        assert await ws.accepted() and await ws.text() is None
        assert ws.closed == (CLOSE_UNAUTHORIZED, BAD_TICKET)
    site.rig.config.set(enabled=False)
    async with site.screen("forged") as off:
        assert await off.accepted() and await off.text() is None
        assert off.closed == (CLOSE_FORBIDDEN, "The iOS Simulator is off for this project (`sim-mirror config`).")
    site.running = False
    async with site.screen("forged") as idle:
        assert await idle.accepted() and await idle.text() is None
        assert idle.closed == (CLOSE_BAD_GATEWAY, NOT_RUNNING)


# -- an agent's routes -----------------------------------------------------------------------------------------------


async def test_an_agent_gets_its_manifest_and_its_calls_answered_as_mcp_results(tmp_path: Path) -> None:
    site = served(tmp_path)
    async with site.http() as http:
        manifest = (await http.get("/api/v1/agent/manifest")).json()
        assert [tool["name"] for tool in manifest["tools"]][:2] == ["sim_device", "sim_snapshot"]
        answer = (await http.post("/api/v1/agent/call", json={"name": "sim_device", "arguments": {}})).json()
    assert answer == {
        "content": [{"type": "text", "text": "No simulator is running here; sim_device boot starts one."}],
        "isError": False,
    }


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"\xff\xfe", b"x" * (BODY_MAX + 1)])
async def test_a_call_that_is_not_a_bounded_json_object_is_an_error_result(tmp_path: Path, body: bytes) -> None:
    site = served(tmp_path)
    async with site.http() as http:
        answer: dict[str, Any] = (await http.post("/api/v1/agent/call", content=body)).json()
    assert answer["isError"] is True and answer["content"][0]["text"] == "a call is a JSON object of at most 256 KB"


async def test_an_agent_the_host_refuses_or_a_server_without_a_runtime_is_answered_so(tmp_path: Path) -> None:
    site = served(tmp_path)
    site.door.refusal = Refused(403, "this agent's token is not for that project")
    async with site.http() as http:
        refused = await http.get("/api/v1/agent/manifest")
        assert (refused.status_code, refused.json()) == (403, {"detail": "this agent's token is not for that project"})
        site.door.refusal = None
        site.running = False
        idle = await http.post("/api/v1/agent/call", json={"name": "sim_device"})
    assert (idle.status_code, idle.json()) == (503, {"detail": NOT_RUNNING})
    assert isinstance(scope(), Scope)
