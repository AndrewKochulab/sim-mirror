# SPDX-License-Identifier: Apache-2.0
"""The app SDK protocol's single source holds: its schemas are valid, every example follows them, every property is
always sent, and what version 1 requires stays required -- the promise an app built with one SDK makes to every
SimMirror that reads it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from sim_mirror.connectors.app import document

APP_SDK = Path(__file__).resolve().parents[2] / "protocol" / "app-sdk" / "v1"
SCHEMAS = {path.name: json.loads(path.read_text()) for path in sorted(APP_SDK.glob("*.schema.json"))}
EXAMPLES = {path.name: json.loads(path.read_text()) for path in sorted((APP_SDK / "examples").glob("*.json"))}


def validator(file: str, definition: str | None = None) -> Draft202012Validator:
    schema = SCHEMAS[file]
    if definition is not None:
        schema = {**schema, "$ref": f"#/$defs/{definition}"}
        schema.pop("properties")
        schema.pop("required")
        schema.pop("type")
    return Draft202012Validator(schema, format_checker=FormatChecker())


def objects(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = {"(root)": schema} if schema.get("type") == "object" else {}
    found.update({name: shape for name, shape in schema.get("$defs", {}).items() if shape.get("type") == "object"})
    return found


def test_every_schema_is_valid_json_schema_and_has_an_id_in_this_folder() -> None:
    assert set(SCHEMAS) == {"hierarchy.schema.json", "listing.schema.json"}
    for name, schema in SCHEMAS.items():
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(f"/protocol/app-sdk/v1/{name}")


def test_every_property_is_always_sent() -> None:
    for name, schema in SCHEMAS.items():
        for shape_name, shape in objects(schema).items():
            assert set(shape["required"]) == set(shape["properties"]), f"{name} {shape_name}"


@pytest.mark.parametrize("example", sorted(EXAMPLES))
def test_every_example_follows_its_schema(example: str) -> None:
    if example == "listing.json":
        check = validator("listing.schema.json")
    elif example.startswith("hierarchy-"):
        check = validator("hierarchy.schema.json")
    else:
        assert example.startswith("error-")
        check = validator("hierarchy.schema.json", "ErrorBody")
    check.validate(EXAMPLES[example])


def test_what_does_not_follow_the_schema_is_refused() -> None:
    listing = {**EXAMPLES["listing.json"], "port": 80, "secret": "short"}
    assert {error.path[0] for error in validator("listing.schema.json").iter_errors(listing)} == {"port", "secret"}
    hierarchy = json.loads(json.dumps(EXAMPLES["hierarchy-uikit.json"]))
    del hierarchy["windows"][0]["nodes"][0]["label"]
    hierarchy["windows"][0]["nodes"][0]["kind"] = "widget"
    refused = sorted(error.validator for error in validator("hierarchy.schema.json").iter_errors(hierarchy))
    assert refused == ["enum", "required"]
    assert not validator("hierarchy.schema.json", "ErrorBody").is_valid({"error": {"code": "teapot", "message": ""}})


def test_version_1_keeps_requiring_what_it_requires() -> None:
    listing = SCHEMAS["listing.schema.json"]
    hierarchy = SCHEMAS["hierarchy.schema.json"]
    defs = hierarchy["$defs"]
    assert listing["required"] == [
        "protocol", "sdk_version", "device_udid", "bundle_id", "name", "pid", "port", "secret", "active", "started_at"
    ]  # fmt: skip
    assert hierarchy["required"] == [
        "protocol", "sdk_version", "app", "screen", "modal", "keyboard", "windows", "truncated", "node_count",
        "capture_ms", "notes",
    ]  # fmt: skip
    assert defs["Node"]["required"] == [
        "kind", "label", "label_source", "identifier", "value", "placeholder", "frame", "traits", "enabled",
        "interactive", "source", "type_name", "children",
    ]  # fmt: skip
    assert {name: shape["required"] for name, shape in defs.items() if "required" in shape and name != "Node"} == {
        "App": ["bundle_id", "name", "pid", "active"],
        "Screen": ["width_pt", "height_pt", "scale", "orientation"],
        "Frame": ["x", "y", "width", "height"],
        "Modal": ["kind", "name"],
        "Keyboard": ["frame"],
        "Window": ["level", "key", "nodes"],
        "ErrorBody": ["error"],
    }


def test_the_daemon_reads_only_what_the_schema_defines_and_knows_every_kind_it_names() -> None:
    hierarchy = SCHEMAS["hierarchy.schema.json"]
    defs = hierarchy["$defs"]
    assert set(hierarchy["properties"]) >= document.HIERARCHY_FIELDS
    assert set(defs["Node"]["properties"]) >= document.NODE_FIELDS
    assert set(document.ROLES) == set(defs["Kind"]["enum"])
    assert set(document.TRAITS) == set(defs["Trait"]["enum"])
    assert set(document.MODALS) == set(defs["ModalKind"]["enum"])


def test_every_value_version_1_names_is_still_named() -> None:
    defs = SCHEMAS["hierarchy.schema.json"]["$defs"]
    enums = {name: set(shape["enum"]) for name, shape in defs.items() if "enum" in shape}
    promised = {
        "Kind": {
            "button", "link", "text", "heading", "image", "field", "secure", "search", "switch", "slider", "stepper",
            "picker", "segments", "tab", "cell", "container", "scroll", "list", "navigation_bar", "tab_bar", "toolbar",
        },
        "LabelSource": {"accessibility", "title", "text", "descendants", "image", "identifier", "type", "tag"},
        "Trait": {"selected", "editing"},
        "NodeSource": {"uikit", "swiftui", "tag"},
        "ModalKind": {"alert", "sheet", "full_screen", "popover"},
        "Orientation": {"portrait", "portrait_upside_down", "landscape_left", "landscape_right", "unknown"},
        "ErrorCode": {
            "bad_request", "unauthorized", "not_found", "method_not_allowed", "inactive", "headers_too_large", "busy",
            "too_large",
        },
    }  # fmt: skip
    assert set(enums) == set(promised)
    for name, values in promised.items():
        assert values <= enums[name], name
