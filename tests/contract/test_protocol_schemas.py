# SPDX-License-Identifier: Apache-2.0
"""The protocol's single source holds: the schemas are valid, what the server builds and what the daemon's routes
answer match them, and the generated constants and enums are the schemas' own."""

from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from sim_mirror import protocol
from sim_mirror.config import provenance
from sim_mirror.config import schema as settings_schema
from sim_mirror.config.settings_store import TomlSettingsStore
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.daemon import passes, tokens
from sim_mirror.daemon.app import build_daemon, create_app
from sim_mirror.protocol import _generated
from sim_mirror.testing.asgi import HOST
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.rig import DeviceRig
from sim_mirror.tools.results import images

PROTOCOL = Path(__file__).resolve().parents[2] / "protocol" / "v1"
SCHEMAS = {path.name: json.loads(path.read_text()) for path in sorted(PROTOCOL.glob("*.schema.json"))}
REGISTRY: Registry[Any] = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def validator(file: str, definition: str | None = None) -> Draft202012Validator:
    schema = SCHEMAS[file]
    if definition is not None:
        schema = {"$ref": f"{schema['$id']}#/$defs/{definition}"}
    return Draft202012Validator(schema, registry=REGISTRY)


def test_every_schema_is_valid_json_schema_and_has_an_id_in_this_folder() -> None:
    assert set(SCHEMAS) == {
        "agent-event.schema.json",
        "client-input.schema.json",
        "common.schema.json",
        "hello.schema.json",
        "http.schema.json",
        "screen-text.schema.json",
        "settings.schema.json",
        "status.schema.json",
        "stream.schema.json",
    }
    for name, schema in SCHEMAS.items():
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(f"/protocol/v1/{name}")


def _enum(file: str, name: str) -> tuple[str, ...]:
    return tuple(SCHEMAS[file]["$defs"][name]["enum"])


def test_the_generated_enums_and_constants_are_the_schemas_own() -> None:
    assert _enum("common.schema.json", "Capability") == _generated.CAPABILITIES
    assert _enum("common.schema.json", "DeviceState") == _generated.DEVICE_STATES
    assert _enum("common.schema.json", "Encoding") == _generated.ENCODINGS
    assert _enum("client-input.schema.json", "KeyName") == _generated.KEY_NAMES
    assert _enum("client-input.schema.json", "PanelButton") == _generated.PANEL_BUTTONS
    assert _enum("settings.schema.json", "SettingEffect") == _generated.SETTING_EFFECTS
    assert _enum("settings.schema.json", "SettingReach") == _generated.SETTING_REACHES
    assert _enum("settings.schema.json", "SettingLayer") == _generated.SETTING_LAYERS
    assert _enum("http.schema.json", "TokenKind") == _generated.TOKEN_KINDS == tokens.KINDS
    assert _enum("http.schema.json", "SessionKind") == _generated.SESSION_KINDS
    assert set(typing.get_args(passes.SessionKind)) == set(_generated.SESSION_KINDS)
    constants = json.loads((PROTOCOL / "constants.json").read_text())
    for key, value in constants.items():
        if not key.startswith("$"):
            assert getattr(protocol, key.upper()) == value
    codes = json.loads((PROTOCOL / "close-codes.json").read_text())["close_codes"]
    assert {getattr(protocol, f"CLOSE_{key.upper()}") for key in codes} == {entry["code"] for entry in codes.values()}
    text = SCHEMAS["client-input.schema.json"]["$defs"]["TextInput"]["properties"]["text"]
    assert text["maxLength"] == protocol.TEXT_MAX_CHARS
    assert SCHEMAS["screen-text.schema.json"]["properties"]["boxes"]["maxItems"] == protocol.SCREEN_TEXT_MAX_BOXES


def test_what_the_server_builds_matches_the_schemas() -> None:
    device = {
        "udid": "D946616B-6E4F-4F5C-8C76-54FAD9B7D702",
        "name": "iPhone 17 Pro",
        "runtime": "iOS 26.5",
        "state": "ready",
        "reason": None,
        "since_ms": 12,
        "viewers": 1,
        "busy": None,
        "created": True,
        "booted_by_us": True,
        "screen": {"points": {"w": 402, "h": 874}, "pixels": {"w": 1206, "h": 2622}, "scale": 3.0},
    }

    def wire(message: object) -> Any:
        """A message as it travels: JSON, where a tuple is an array."""
        return json.loads(json.dumps(message))

    validator("status.schema.json").validate(wire(protocol.status_event(device)))  # type: ignore[arg-type]
    validator("common.schema.json", "Device").validate(device)
    hello = wire(
        protocol.server_hello(encodings=["h264", "jpeg"], connector="idb", capabilities=_generated.CAPABILITIES)
    )
    validator("hello.schema.json").validate(hello)
    validator("hello.schema.json", "ServerHello").validate(hello)
    validator("hello.schema.json", "ClientHello").validate({"type": "hello", "v": 1, "encodings": ["jpeg"]})
    validator("stream.schema.json").validate(wire(protocol.stream_start("jpeg")))
    events = validator("agent-event.schema.json")
    intent = protocol.agent_intent(
        event_id="a1",
        agent={"key": "k", "title": "Claude Code"},
        kind="swipe",
        duration_ms=300,
        points=[(0.5, 0.8), (0.5, 0.2)],
        caption="swipe",
        lead_ms=250,
        linger_ms=60_000,
    )
    events.validate(wire(intent))
    events.validate(wire(protocol.agent_done("a1", True)))
    events.validate(wire(protocol.agent_working("w1", {"key": "k", "title": "Claude Code"}, 60_000, ongoing=True)))
    for wrong in ({"type": "agent", "id": "w1", "phase": "working", "agent": {"key": "k", "title": "t"}},):
        with pytest.raises(ValidationError):
            events.validate(wrong)
    text = validator("screen-text.schema.json")
    boxes = [protocol.text_box("Sign in", 0.93, 0.1, 0.2, 0.3, 0.04)] * (protocol.SCREEN_TEXT_MAX_BOXES + 1)
    read = wire(protocol.screen_text("t1", boxes, hold_ms=60_000))
    text.validate(read)
    assert len(read["boxes"]) == protocol.SCREEN_TEXT_MAX_BOXES
    text.validate(wire(protocol.screen_text("t2")))


@pytest.mark.parametrize(
    "message",
    [
        {"type": "touch", "phase": "down", "nx": 0.5, "ny": 1},
        {"type": "scroll", "nx": 0, "ny": 0.5, "dy": -120.5},
        {"type": "button", "name": "home"},
        {"type": "key", "name": "return"},
        {"type": "text", "text": "café 😀"},
        {"type": "appearance", "mode": "dark"},
    ],
)
def test_the_inputs_a_viewer_sends_are_valid(message: dict[str, Any]) -> None:
    validator("client-input.schema.json").validate(message)


@pytest.mark.parametrize(
    ("file", "message"),
    [
        ("client-input.schema.json", {"type": "touch", "phase": "hover", "nx": 0.5, "ny": 0.5}),
        ("client-input.schema.json", {"type": "touch", "phase": "down", "nx": 1.5, "ny": 0.5}),
        ("client-input.schema.json", {"type": "button", "name": "apple_pay"}),
        ("client-input.schema.json", {"type": "text", "text": ""}),
        ("client-input.schema.json", {"type": "text", "text": "a\x00b"}),
        ("client-input.schema.json", {"type": "install", "path": "/x.app"}),
        ("agent-event.schema.json", {"type": "agent", "id": "a", "phase": "done"}),
        ("status.schema.json", {"type": "status", "udid": "U"}),
        ("stream.schema.json", {"type": "stream", "encoding": "vp9"}),
        ("screen-text.schema.json", {"type": "screen_text", "id": "t1", "boxes": []}),
        ("screen-text.schema.json", {"type": "screen_text", "id": "t1", "hold_ms": -1, "boxes": []}),
        (
            "screen-text.schema.json",
            {
                "type": "screen_text",
                "id": "t1",
                "hold_ms": 0,
                "boxes": [
                    {
                        **{"text": "Sign in", "confidence": 0.9, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04},
                        "confidence": 1.5,
                    }
                ],
            },
        ),
        (
            "screen-text.schema.json",
            {
                "type": "screen_text",
                "id": "t1",
                "hold_ms": 0,
                "boxes": [
                    {**{"text": "Sign in", "confidence": 0.9, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}, "x": -0.1}
                ],
            },
        ),
        (
            "screen-text.schema.json",
            {
                "type": "screen_text",
                "id": "t1",
                "hold_ms": 0,
                "boxes": [{"text": "Sign in", "confidence": 0.9, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}] * 201,
            },
        ),
    ],
)
def test_messages_the_protocol_does_not_allow_are_refused(file: str, message: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validator(file).validate(message)


def test_every_settings_rule_and_what_the_settings_table_says_are_the_protocols_own() -> None:
    rule = validator("settings.schema.json", "RuleSpec")
    for setting in settings_schema.SETTINGS:
        rule.validate(setting.rule.spec())
    for wrong in ({"kind": "whole", "low": 1}, {"kind": "colour"}, {"kind": "text", "format": "name"}):
        with pytest.raises(ValidationError):
            rule.validate(wrong)
    value = validator("settings.schema.json", "SettingValue")
    for setting in settings_schema.SETTINGS:
        value.validate(list(setting.default) if isinstance(setting.default, tuple) else setting.default)
    assert set(typing.get_args(settings_schema.Effect)) == set(_generated.SETTING_EFFECTS)
    assert set(typing.get_args(settings_schema.Reach)) == set(_generated.SETTING_REACHES)
    assert provenance.LAYERS == _generated.SETTING_LAYERS


async def test_what_the_daemons_routes_answer_a_host_a_viewer_and_an_agent_matches_the_schemas(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    source = TomlConfigSource(tmp_path / "config.toml", env={})
    daemon = build_daemon(
        config=source,
        state=rig.state,
        memory=rig.memory,
        tokens=tokens.TokenStore(tmp_path / "secrets"),
        port=7466,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        static_dir=None,
        settings=TomlSettingsStore(source),
        clock=rig.clock,
        sleep=no_wait,
    )
    app = create_app(daemon)

    def answer(response: httpx.Response, definition: str, file: str = "http.schema.json") -> Any:
        """Check the envelope and its data, and answer the data."""
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"ok", "data"} and body["ok"] is True
        validator(file, definition).validate(body["data"])
        return body["data"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as http:
        admin = {"authorization": f"Bearer {daemon.tokens.admin_token()}"}
        answer(await http.get("/healthz"), "Health")
        made = await http.post("/api/v1/admin/tokens", headers=admin, json={"kind": "host", "scopes": ["notes:*"]})
        host = answer(made, "MadeToken")
        health = answer(await http.get("/healthz", params={"nonce": "n0nce", "token_id": host["id"]}), "Health")
        assert health["proof"] and health["token_proof"]
        mine = {"authorization": f"Bearer {host['token']}"}
        answer(await http.get("/api/v1/host", headers=mine), "TokenRecord")

        scope = "/api/v1/scopes/notes:42"
        answer(await http.get(scope, headers=mine), "ScopeStatus", "status.schema.json")
        answer(await http.post(scope, headers=mine), "Started", "status.schema.json")
        devices = answer(await http.get(scope + "/devices", headers=mine), "DeviceList")
        choice = {"udid": devices["devices"][0]["udid"]}
        answer(await http.put(scope + "/device", headers=mine, json=choice), "Chosen")
        answer(await http.get(scope + "/settings", headers=mine), "SettingsView", "settings.schema.json")
        ticket = answer(await http.post(scope + "/embed-tickets", headers=mine), "EmbedTicket")
        code = ticket["url"].split("#ticket=", 1)[1]
        answer(await http.post("/api/v1/auth/exchange", json={"code": code}), "Exchanged")

        agent = answer(
            await http.post("/api/v1/host/tokens", headers=mine, json={"kind": "agent", "scopes": ["notes:42"]}),
            "MadeToken",
        )
        answer(await http.get("/api/v1/host/tokens", headers=mine), "TokenList")
        acting = {"authorization": f"Bearer {agent['token']}"}
        answer(await http.post("/api/v1/agent/lease", headers=acting), "Lease")
        manifest = await http.get("/api/v1/agent/manifest", headers=acting)
        validator("http.schema.json", "AgentManifest").validate(manifest.json())
        called = await http.post("/api/v1/agent/call", headers=acting, json={"name": "sim_device", "arguments": {}})
        validator("http.schema.json", "ToolResult").validate(called.json())
        answer(await http.delete(f"/api/v1/host/tokens/{agent['id']}", headers=mine), "Revoked")
        answer(await http.delete(scope, headers=mine), "Stopped")

        refused = validator("http.schema.json", "Refusal")
        unknown = await http.get(scope)
        assert unknown.status_code == 401
        refused.validate(unknown.json())
        malformed = await http.put(scope + "/device", headers=mine, json={"udid": ""})
        assert malformed.status_code == 422 and isinstance(malformed.json()["detail"], list)
        refused.validate(malformed.json())

    validator("http.schema.json", "ToolResult").validate(images("the screen", [b"\xff\xd8\xff"]))
