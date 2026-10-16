# SPDX-License-Identifier: Apache-2.0
"""What a host embedding SimMirror can rely on of a real device, whatever the host is.

Each test is driven the way a host drives SimMirror: a `Runtime` built from `sim_mirror.api` with the host's seams,
the person, screen and agent routers mounted under the host's own prefix, and the runtime's `call`, `reconcile`,
`start` and `close`. Under it is SimMirror's own testing Mac -- devicectl answering as two iPhones printed it, a phone
connector that is view only until WebDriverAgent is set up -- and nothing real. A device's state is looked at
directly only to check what the routes said.

The contract: a real device is used only once a person turns real devices on and picks one; two scopes that may not
share, or another host on this Mac, never drive one phone at once; what an agent is offered follows what the phone
can do now; real devices switched off end their sessions there and then, and WebDriverAgent switched off leaves a
phone view only there and then; a viewer from before real devices still
watches and touches one, and comes first; an agent is drawn as the host authenticated it; a phone that is not there
is refused, never replaced by a simulator; what was changed on a phone is put back, after a disconnect or a crash; and
nothing ever asks a phone to shut down, restart or be erased.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from sim_mirror import api
from sim_mirror.connectors.base import ConnectorUnavailable
from sim_mirror.protocol import CLOSE_FORBIDDEN, CLOSE_RESTARTING
from sim_mirror.storage.claims import Claims
from sim_mirror.testing.asgi import HOST, AsgiSocket
from sim_mirror.testing.fakes import (
    BOOTED_UDID,
    DEVICECTL_SUCCESS,
    FULL_CONTROL,
    PHONE_UDID,
    FakeConnector,
    FakeInputPause,
    fixture_json,
    no_wait,
)
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig
from sim_mirror.testing.vision import FakeTextRecognizer

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "/host/{scope_id}/device"
TOKEN, AGENT = "token-7", "agent-7"
#: What a lifecycle must never ask devicectl of a phone.
NEVER = frozenset({"reboot", "restart", "shutdown", "erase", "uninstall", "unpair", "delete"})
UNPLUGGED = "Test iPhone is not connected: plug it in and unlock it, or pick a simulator."
APPEARANCE = ("devicectl", "-q", "device", "settings", "appearance")


def scope_of(scope_id: str) -> api.Scope:
    """A host's scope: its id names its group before the dash, so `a-1` and `a-2` share and `b-1` is another's."""
    group = scope_id.split("-")[0]
    return api.Scope(id=scope_id, group=group, label=f"{group} · {scope_id}")


class Door:
    """A host's authenticator: a person is whoever names a scope; an agent is whoever its token says, never what it
    says of itself."""

    async def person(self, request: Request, scope_id: str) -> api.Person:
        return api.Person(scope_of(scope_id))

    async def agent(self, request: Request) -> api.Caller:
        if request.headers.get("x-token") != TOKEN:
            raise api.Refused(401, "no such agent")
        return api.Caller(scope_of(request.headers["x-scope"]), key=AGENT, title="Agent · checkout")

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> api.Admission:
        return api.Admission(scope_of(scope_id))


def _open_enums(schema: Any) -> Any:
    """A schema with its enums read as open, as `docs/stability.md` tells every client to read them."""
    if isinstance(schema, dict):
        return {key: _open_enums(value) for key, value in schema.items() if key != "enum"}
    if isinstance(schema, list):
        return [_open_enums(value) for value in schema]
    return schema


def _validators(schemas: dict[str, Any]) -> Callable[[str, str | None], Draft202012Validator]:
    registry: Registry[Any] = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )

    def validator(file: str, definition: str | None = None) -> Draft202012Validator:
        schema = schemas[file]
        if definition is not None:
            schema = {"$ref": f"{schema['$id']}#/$defs/{definition}"}
        return Draft202012Validator(schema, registry=registry)

    return validator


#: Protocol v1 as 1.0 wrote it -- what a viewer from before real devices was built against -- and as it is now.
OLD = _validators(_open_enums(json.loads((ROOT / "compat" / "surface-v1.json").read_text())["protocol"]["schemas"]))
NOW = _validators(
    {path.name: json.loads(path.read_text()) for path in sorted((ROOT / "protocol" / "v1").glob("*.schema.json"))}
)


class Host:
    """A host application embedding SimMirror, with real devices turned on by its person and agents choosing off."""

    def __init__(
        self,
        root: Path,
        *,
        phone_can: frozenset[Any] = FULL_CONTROL,
        hold: bool = False,
        may_share: Callable[[api.Scope, api.Scope], bool] | None = None,
        pause: api.InputPause | None = None,
        sleep: Callable[[float], Awaitable[None]] = no_wait,
    ) -> None:
        self.phone = FakeConnector("phone", kinds=frozenset({"physical"}), capabilities=phone_can, hold=hold)
        self.rig = DeviceRig(root, phone=self.phone)
        # The person has turned real devices on; agents choosing one stays off, as an embedded host starts.
        self.rig.config.set(real_devices=True, real_devices_agents_choose=False, real_devices_team_id="ABCDE12345")
        self.rig.xcrun.with_devicectl()
        self.sleep = sleep
        self.pause = pause
        self.may_share = may_share
        self.runtime = self.build()
        self.app = FastAPI()
        self.app.include_router(api.create_http_router(lambda: self.runtime, Door()), prefix=PREFIX)
        self.app.include_router(api.create_socket_router(lambda: self.runtime, Door()), prefix=PREFIX)
        self.app.include_router(api.create_agent_router(lambda: self.runtime, Door()), prefix="/host/agent")

    def build(self) -> api.Runtime:
        """A runtime over this host's seams: the one it starts with, or the next, after it crashed or restarted."""
        rig = self.rig
        return api.Runtime.build(
            config=rig.config,
            state=rig.state,
            policy=rig.policy,
            memory=rig.memory,
            copy=rig.copy,
            may_share=self.may_share,
            pause=self.pause,
            registry=rig.registry,
            claims=rig.claims,
            xcrun=rig.xcrun,
            vision=FakeTextRecognizer(),
            clock=rig.clock,
            sleep=self.sleep,
            platform="darwin",
        )

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=f"http://{HOST}")

    def route(self, scope_id: str, path: str = "") -> str:
        return PREFIX.format(scope_id=scope_id) + path

    async def pick(self, http: httpx.AsyncClient, scope_id: str = "a-1", udid: str = PHONE_UDID) -> httpx.Response:
        return await http.put(self.route(scope_id, "/device"), json={"udid": udid})

    async def start(self, http: httpx.AsyncClient, scope_id: str = "a-1") -> httpx.Response:
        """A person's start, waited for until the device is up or has failed."""
        answer = await http.post(self.route(scope_id))
        instance = self.runtime.manager.instance(scope_of(scope_id))
        if answer.status_code == 200 and instance is not None and instance.task is not None:
            await instance.task
        return answer

    async def status(self, http: httpx.AsyncClient, scope_id: str = "a-1") -> dict[str, Any]:
        answer: dict[str, Any] = (await http.get(self.route(scope_id))).json()["data"]
        return answer

    async def call(self, name: str, arguments: dict[str, Any], scope_id: str = "a-1") -> tuple[bool, str]:
        async with self.http() as http:
            answer = (
                await http.post(
                    "/host/agent/call",
                    json={"name": name, "arguments": arguments},
                    headers={"x-token": TOKEN, "x-scope": scope_id},
                )
            ).json()
        return answer["isError"], answer["content"][0]["text"]

    async def offered(self, scope_id: str = "a-1") -> list[str]:
        async with self.http() as http:
            manifest = (await http.get("/host/agent/manifest", headers={"x-token": TOKEN, "x-scope": scope_id})).json()
        return [tool["name"] for tool in manifest["tools"]]

    @asynccontextmanager
    async def viewer(self, ticket: str, scope_id: str = "a-1") -> AsyncIterator[tuple[AsgiSocket, dict[str, Any]]]:
        """A viewer from before real devices: it says the hello 1.0 knew, and is sent what 1.0 promised it -- with
        more beside it, which it ignores. Yields the socket and the server's hello."""
        async with AsgiSocket(self.app, self.route(scope_id, "/screen"), query=f"ticket={ticket}") as ws:
            assert await ws.accepted()
            hello = await ws.text()
            OLD("hello.schema.json", "ServerHello").validate(hello)
            NOW("hello.schema.json", "ServerHello").validate(hello)
            await ws.say({"type": "hello", "v": 1, "encodings": ["jpeg"]})
            assert await ws.text() == {"type": "stream", "encoding": "jpeg"}
            status = await ws.text()
            OLD("status.schema.json", None).validate(status)
            NOW("status.schema.json", None).validate(status)
            yield ws, hello

    def unplug(self) -> None:
        """The cable comes out: devicectl no longer reaches the phone, and its connector cannot either."""
        listing = fixture_json("devicectl-devices.json")
        for device in listing["result"]["devices"]:
            if device["hardwareProperties"].get("udid") == PHONE_UDID:
                device["connectionProperties"]["transportType"] = None
                device["properties"]["connection"]["transportType"] = None
        self.rig.xcrun.on("devicectl", "-q", "list", "devices", out=json.dumps(listing))
        self.rig.xcrun.on(*APPEARANCE, rc=1, err="ERROR: The device is not connected.")
        self.phone.fail = ConnectorUnavailable("Test iPhone is not connected: plug it in and unlock it", 409)

    def plug_in(self) -> None:
        self.rig.xcrun.with_devicectl()
        self.rig.xcrun.on(*APPEARANCE, out=json.dumps(DEVICECTL_SUCCESS))
        self.phone.fail = None

    def looks(self) -> list[str]:
        """Each light or dark the phone was told to be, in order."""
        return [argv[8] for argv in self.rig.argv() if argv[: len(APPEARANCE)] == APPEARANCE and argv[7] == "--mode"]

    def asked_of_the_phone(self) -> list[tuple[str, ...]]:
        """Every devicectl call, and every simctl call that names the phone."""
        return [
            argv for argv in self.rig.argv() if argv[0] == "devicectl" or (argv[0] == "simctl" and PHONE_UDID in argv)
        ]


async def _texts(ws: AsgiSocket, until: Callable[[dict[str, Any]], bool], limit: int = 2000) -> list[dict[str, Any]]:
    """The text messages a viewer is sent until one `until` accepts, frames skipped."""
    seen: list[dict[str, Any]] = []
    for _ in range(limit):
        message = await ws.next()
        if message["type"] != "websocket.send" or "text" not in message:
            continue
        seen.append(json.loads(message["text"]))
        if until(seen[-1]):
            return seen
    raise AssertionError(f"not sent within {limit} messages: {seen[-5:]}")


def _never_asked_to_go(host: Host) -> None:
    asked = host.asked_of_the_phone()
    assert all(argv[0] == "devicectl" for argv in asked), f"simctl was asked about the phone: {asked}"
    assert not [argv for argv in asked if NEVER & set(argv)], "a phone is never shut down, restarted or erased"


# -- choosing a phone ------------------------------------------------------------------------------------------------


async def test_a_phone_is_used_only_once_a_person_turns_real_devices_on_and_picks_it(tmp_path: Path) -> None:
    embedded = api.SimConfig.defaults("embedded")
    assert (embedded.real_devices, embedded.real_devices_agents_choose, embedded.wda_enabled) == (False, False, False)
    host = Host(tmp_path)
    refused, said = await host.call("sim_device", {"action": "choose", "udid": PHONE_UDID})
    assert refused and said == host.rig.copy.agents_may_not_choose()
    async with host.http() as http:
        assert (await host.pick(http)).status_code == 200
        assert (await host.call("sim_device", {}))[1].startswith("No session is open on the real device here")
        assert (await host.start(http)).status_code == 200
        device = (await host.status(http))["device"]
    assert (device["udid"], device["kind"], device["connection"], device["state"]) == (
        PHONE_UDID,
        "physical",
        "usb",
        "ready",
    )
    assert host.rig.idb.attached == [], "no simulator was brought up for it"


async def test_two_scopes_that_may_not_share_never_drive_one_phone_and_those_that_may_share_one_session(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path, may_share=lambda one, other: one.group == other.group)
    async with host.http() as http:
        assert (await host.pick(http, "b-1")).status_code == 200, "nobody drives it yet"
        await host.pick(http, "a-1")
        assert (await host.start(http, "a-1")).status_code == 200
        taken = await host.pick(http, "b-1")
        elsewhere = host.rig.copy.in_use_elsewhere_of_kind("physical")
        assert (taken.status_code, taken.json()["detail"]) == (409, elsewhere) and "simulator is" not in elsewhere
        started = await host.start(http, "b-1")
        assert (started.status_code, started.json()["detail"]) == (409, elsewhere)
        listed = (await http.get(host.route("b-1", "/devices"))).json()["data"]["devices"]
        assert PHONE_UDID not in [choice["udid"] for choice in listed], "another's phone is not offered"
        assert (await host.status(http, "b-1"))["device"] is None, "and nothing is put in its place"
        await host.pick(http, "a-2")
        assert (await host.start(http, "a-2")).status_code == 200
    assert host.phone.attached == [PHONE_UDID] and host.rig.idb.attached == []


async def test_a_phone_another_host_on_this_mac_drives_is_refused_saying_who_and_taken_once_it_lets_go(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    host.rig.alive_pids.add(9999)

    async def started(pid: int) -> str | None:
        return f"started-{pid}"

    other = Claims(tmp_path / "claims", owner="Other", pid=9999, pid_alive=lambda pid: True, start_time=started)
    await other.acquire(PHONE_UDID)
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
        device = (await host.status(http))["device"]
        claimed = host.rig.copy.claimed_of_kind("Other", 9999, "physical")
        assert device["state"] == "failed" and device["reason"] == claimed and "this simulator" not in claimed
        assert host.phone.attached == []
        refused, said = await host.call("sim_device", {"action": "boot"})
        assert refused and said == f"the device failed: {claimed}"
        await other.release(PHONE_UDID)
        await host.start(http)
        assert (await host.status(http))["device"]["state"] == "ready"
    assert await other.holder(PHONE_UDID) is not None, "the other host is refused it now"


# -- what agents are offered -----------------------------------------------------------------------------------------


async def test_what_agents_are_offered_and_viewers_told_follows_what_the_phone_can_do_now(tmp_path: Path) -> None:
    host = Host(tmp_path, phone_can=VIEW_ONLY)
    async with host.http() as http:
        await host.pick(http)
        ticket = (await host.start(http)).json()["data"]["ticket"]
        assert "sim_act" not in await host.offered()
        async with host.viewer(ticket) as (ws, hello):
            assert "input_touch" not in hello["capabilities"]
            # WebDriverAgent was set up since: the phone's session is started over, and can now be touched.
            host.phone.capabilities = FULL_CONTROL
            refused, said = await host.call("sim_device", {"action": "restart"})
            assert not refused and said.startswith("reconnected: its session was started over")
            while ws.closed is None:
                await ws.next()
            assert ws.closed[0] == CLOSE_RESTARTING, "told to come back, and hear a hello that says what is so"
        assert "sim_act" in await host.offered()
        ticket = (await host.start(http)).json()["data"]["ticket"]
        async with host.viewer(ticket) as (_, hello):
            assert "input_touch" in hello["capabilities"]
        # And WebDriverAgent gone again leaves it view only, refusing a touch rather than pretending.
        host.phone.capabilities = VIEW_ONLY
        await host.call("sim_device", {"action": "restart"})
        assert "sim_act" not in await host.offered()
        refused, said = await host.call("sim_act", {"steps": [{"tap": [100, 200]}]})
        assert refused and said.startswith("sim_act needs") and "input_touch" in said
    _never_asked_to_go(host)


async def test_real_devices_switched_off_during_a_call_end_the_session_there_and_leave_the_phone_be(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()

    async def held(seconds: float) -> None:
        await gate.wait()

    host = Host(tmp_path, hold=True, sleep=held)
    async with host.http() as http:
        await host.pick(http)
        assert (await http.post(host.route("a-1"))).status_code == 200
        calling = asyncio.create_task(host.call("sim_device", {"action": "boot"}))
        while not host.phone.attached:
            await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)
        host.rig.config.set(real_devices=False)
        await host.runtime.reconcile("a")
        gate.set()
        refused, said = await calling
        off = host.rig.copy.kind_unavailable("physical")
        assert (refused, said) == (True, f"the device stopped: {off}")
        assert (await host.status(http))["device"] is None
        # Its agents keep sim_device, to pick a simulator with, and are told why the phone is not there.
        assert "sim_device" in await host.offered()
        assert await host.call("sim_device", {"action": "boot"}) == (True, off)
    assert host.rig.idb.attached == [], "never a simulator in its place"
    _never_asked_to_go(host)


async def test_real_devices_switched_off_close_a_phone_s_screens_saying_why(tmp_path: Path) -> None:
    host = Host(tmp_path)
    async with host.http() as http:
        await host.pick(http)
        ticket = (await host.start(http)).json()["data"]["ticket"]
        async with host.viewer(ticket) as (ws, _):
            host.rig.config.set(real_devices=False)
            await host.runtime.reconcile()
            while ws.closed is None:
                await ws.next()
        assert ws.closed == (CLOSE_FORBIDDEN, host.rig.copy.kind_unavailable("physical"))
        assert (await host.status(http))["device"] is None
    assert host.phone.closed == [PHONE_UDID]
    _never_asked_to_go(host)


async def test_webdriveragent_turned_off_leaves_the_phone_view_only_at_once_its_screens_and_agents_told(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    async with host.http() as http:
        await host.pick(http)
        ticket = (await host.start(http)).json()["data"]["ticket"]
        assert "sim_act" in await host.offered()
        async with host.viewer(ticket) as (ws, hello):
            assert "input_touch" in hello["capabilities"]
            host.rig.config.set(wda_enabled=False)
            await host.runtime.reconcile("a")
            while ws.closed is None:
                await ws.next()
            assert ws.closed[0] == CLOSE_RESTARTING, "told to come back, and hear a hello that says what is so"
        assert "sim_act" not in await host.offered()
        assert await host.call("sim_act", {"steps": [{"tap": [100, 200]}]}) == (True, host.rig.copy.wda_off())
        assert host.phone.engine.hid_events == []
        ticket = (await host.start(http)).json()["data"]["ticket"]
        async with host.viewer(ticket) as (_, hello):
            assert "input_touch" not in hello["capabilities"]
        device = (await host.status(http))["device"]
        assert (device["udid"], device["state"]) == (PHONE_UDID, "ready"), "the same session, started over"
    assert host.phone.attached == [PHONE_UDID, PHONE_UDID] and host.phone.closed == [PHONE_UDID]
    _never_asked_to_go(host)


async def test_webdriveragent_turned_off_where_nothing_reconciles_stops_a_batch_and_the_reaper_drops_touch(
    tmp_path: Path,
) -> None:
    async def turning_off(seconds: float) -> None:
        if seconds == 0.01:
            host.rig.config.set(wda_enabled=False)

    host = Host(tmp_path, sleep=turning_off)
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
    steps = [{"tap": [100, 200]}, {"pause": 10}, {"tap": [100, 200]}]
    refused, said = await host.call("sim_act", {"steps": steps, "snapshot": "none"})
    off = f"error step 3: {host.rig.copy.wda_off()}"
    assert refused and said.splitlines() == ["ok tap (100,200)", "ok pause 10ms", off]
    touched = len(host.phone.engine.hid_events)
    assert touched and "sim_act" in await host.offered(), "offered until the session is started over"
    assert await host.runtime.manager.reap() == []
    assert "sim_act" not in await host.offered() and host.phone.attached == [PHONE_UDID, PHONE_UDID]
    assert len(host.phone.engine.hid_events) == touched
    _never_asked_to_go(host)


# -- people and agents -----------------------------------------------------------------------------------------------


async def test_a_viewer_from_before_real_devices_drives_a_phone_first_and_sees_the_agent_the_host_authenticated(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    async with host.http() as http:
        await host.pick(http)
        ticket = (await host.start(http)).json()["data"]["ticket"]
    instance = host.runtime.manager.instance(scope_of("a-1"))
    assert instance is not None
    async with host.viewer(ticket) as (ws, _):
        await ws.say({"type": "touch", "phase": "down", "nx": 0.5, "ny": 0.5})
        await ws.say({"type": "touch", "phase": "up", "nx": 0.5, "ny": 0.5})
        for _ in range(200):
            await asyncio.sleep(0)
        touched = len(host.phone.engine.hid_events)
        assert touched and instance.person_touch_at == host.rig.clock.now
        # The person's hand was on the screen a moment ago: the agent waits for it to be still.
        acting = asyncio.create_task(host.call("sim_act", {"steps": [{"tap": [100, 200]}], "snapshot": "none"}))
        for _ in range(200):
            await asyncio.sleep(0)
        assert not acting.done() and len(host.phone.engine.hid_events) == touched
        host.rig.clock.now += 0.5
        refused, said = await acting
        assert not refused and said.startswith("ok tap (100,200)")
        assert len(host.phone.engine.hid_events) > touched
        told = await _texts(ws, lambda event: event.get("type") == "agent" and event.get("phase") == "intent")
    agents = [event["agent"] for event in told if event.get("type") == "agent" and "agent" in event]
    assert agents and all(agent == {"key": AGENT, "title": "Agent · checkout"} for agent in agents)
    for event in told:
        if event.get("type") == "agent":
            NOW("agent-event.schema.json", None).validate(event)


async def test_a_host_s_pause_keeps_agents_off_a_phone_a_person_has_in_hand(tmp_path: Path) -> None:
    async def moving(seconds: float) -> None:
        host.rig.clock.now += seconds
        await asyncio.sleep(0)

    pause = FakeInputPause()
    host = Host(tmp_path, pause=pause, sleep=moving)
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
    pause.held.add(PHONE_UDID)
    refused, said = await host.call("sim_act", {"steps": [{"tap": [100, 200]}], "snapshot": "none"})
    assert refused and said == "a person has paused agents on this device; try again once they resume"
    assert host.phone.engine.hid_events == [] and "sim_act" in await host.offered(), "offered, and answered why"
    pause.held.clear()
    assert (await host.call("sim_act", {"steps": [{"tap": [100, 200]}], "snapshot": "none"}))[1].startswith("ok tap")


# -- a phone that goes away -----------------------------------------------------------------------------------------


async def test_a_phone_that_is_not_there_is_refused_and_never_replaced_by_a_simulator(tmp_path: Path) -> None:
    host = Host(tmp_path)
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
        host.unplug()
        refused, said = await host.call("sim_device", {"action": "restart"})
        assert refused and said.startswith("could not reconnect to the device: Test iPhone is not connected")
        started = await host.start(http)
        assert (started.status_code, started.json()["detail"]) == (409, UNPLUGGED)
        assert await host.call("sim_device", {"action": "boot"}) == (True, UNPLUGGED)
        status = await host.status(http)
        assert status["device"] is None and status["reason"] is None
        devices = (await http.get(host.route("a-1", "/devices"))).json()["data"]["devices"]
        listed = {choice["udid"]: choice for choice in devices}
        assert listed[PHONE_UDID]["state"] == "Disconnected", "it is still the scope's, shown as not there"
        host.plug_in()
        assert (await host.start(http)).status_code == 200
        assert (await host.status(http))["device"]["udid"] == PHONE_UDID
    assert host.rig.idb.attached == [] and not [argv for argv in host.rig.argv() if argv[:2] == ("simctl", "boot")]
    _never_asked_to_go(host)


async def test_what_an_agent_changed_on_a_phone_is_put_back_after_a_disconnect_and_after_a_crash(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    journal = host.rig.state.run_dir() / "device-changes.json"
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
        assert (await host.call("sim_device", {"action": "appearance", "mode": "light"}))[0] is False
        assert host.looks() == ["light"], "it was dark"
        # Unplugged while it is driven: what it had cannot be put back now, so it is kept written down.
        host.unplug()
        assert (await host.call("sim_device", {"action": "restart"}))[0] is True
        assert (await host.start(http)).status_code == 409
        assert PHONE_UDID in json.loads(journal.read_text())
        # Plugged back in and driven again, then let go: what the first session changed is put back then.
        host.plug_in()
        await host.start(http)
        stopped = await http.delete(host.route("a-1"), params={"shutdown": "true"})
        assert stopped.json()["data"] == {"stopped": True, "shut_down": False}, "asked to, a phone is not shut down"
        assert host.looks()[-1] == "dark" and json.loads(journal.read_text()) == {}
        # Changed again, and the host crashes with the phone unplugged: the next start keeps it, the one after it
        # -- the phone plugged back in -- puts it back.
        await host.start(http)
    await host.call("sim_device", {"action": "appearance", "mode": "light"})
    host.unplug()
    for crashed in (True, False):
        host.runtime = host.build()
        await host.runtime.start()
        assert (PHONE_UDID in json.loads(journal.read_text())) is crashed
        await host.runtime.close()
        host.plug_in()
    assert host.looks()[-3:] == ["light", "dark", "dark"], "changed, tried while unplugged, put back"
    _never_asked_to_go(host)


async def test_no_way_a_host_lets_go_of_a_phone_asks_it_to_shut_down_restart_or_be_erased(tmp_path: Path) -> None:
    host = Host(tmp_path)
    async with host.http() as http:
        await host.pick(http)
        await host.start(http)
        await host.call("sim_device", {"action": "boot"})
        await host.call("sim_device", {"action": "restart"})
        await host.call("sim_device", {"action": "choose", "udid": BOOTED_UDID})
        await host.pick(http)
        await host.start(http)
        await http.delete(host.route("a-1"), params={"shutdown": "true"})
        await host.start(http)
        await host.runtime.reconcile()
        host.rig.config.set(real_devices=False)
        await host.runtime.reconcile()
        host.rig.config.set(real_devices=True)
        await host.start(http)
        # The reaper asks whether a device was shut down outside SimMirror: a phone is never taken for one.
        assert await host.runtime.manager.reap() == [] and (await host.status(http))["device"]["state"] == "ready"
        refused, said = await host.call("sim_device", {"action": "stop", "shutdown": True})
        assert not refused and "a real device is never shut down by SimMirror, so it stays on" in said
        assert (await host.status(http))["device"] is None
        await host.start(http)
    await host.runtime.close()
    assert host.phone.closed and len(host.phone.closed) == len(host.phone.attached)
    _never_asked_to_go(host)
