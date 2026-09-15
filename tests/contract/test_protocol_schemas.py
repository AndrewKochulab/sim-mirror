# SPDX-License-Identifier: Apache-2.0
"""The protocol's single source holds: the schemas are valid, what the server builds matches them, and the generated
constants and enums are the schemas' own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from sim_mirror import protocol
from sim_mirror.protocol import _generated

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
    constants = json.loads((PROTOCOL / "constants.json").read_text())
    for key, value in constants.items():
        if not key.startswith("$"):
            assert getattr(protocol, key.upper()) == value
    codes = json.loads((PROTOCOL / "close-codes.json").read_text())["close_codes"]
    assert {getattr(protocol, f"CLOSE_{key.upper()}") for key in codes} == {entry["code"] for entry in codes.values()}
    text = SCHEMAS["client-input.schema.json"]["$defs"]["TextInput"]["properties"]["text"]
    assert text["maxLength"] == protocol.TEXT_MAX_CHARS


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
    )
    events.validate(wire(intent))
    events.validate(wire(protocol.agent_done("a1", True)))


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
    ],
)
def test_messages_the_protocol_does_not_allow_are_refused(file: str, message: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validator(file).validate(message)
