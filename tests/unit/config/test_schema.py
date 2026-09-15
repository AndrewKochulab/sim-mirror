# SPDX-License-Identifier: Apache-2.0
"""The settings table: every setting checked by one rule, refusals said the same way everywhere, and config.toml's
nesting derived from it."""

from __future__ import annotations

from typing import Any

import pytest

from sim_mirror.config import schema
from sim_mirror.config.schema import (
    SETTINGS,
    AbsolutePath,
    Choice,
    ConfigurationName,
    Flag,
    LoopbackHost,
    Name,
    Origins,
    Whole,
)


def host_naming(setting: schema.Setting) -> str:
    return f"simulator.{setting.key}"


def test_every_default_is_valid_for_both_profiles_and_keys_and_paths_are_unique() -> None:
    assert len(schema.BY_KEY) == len(SETTINGS) == len(schema.BY_PATH)
    for profile in schema.PROFILES:
        assert schema.errors(schema.defaults(profile)) == []
    assert schema.defaults("standalone")["enabled"] is True and schema.defaults("embedded")["enabled"] is False
    assert schema.defaults("standalone")["build_tools"] is False and schema.defaults("embedded")["build_tools"] is True
    assert schema.defaults("embedded")["stream_fps"] == 30
    assert all(setting.doc.endswith(".") for setting in SETTINGS)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("enabled", "yes", "simulator.enabled must be true or false"),
        ("agent_cursor", 1, "simulator.agent_cursor must be true or false"),
        ("stream_fps", 4, "simulator.stream_fps must be a whole number between 5 and 60"),
        ("stream_fps", 61, "between 5 and 60"),
        ("stream_fps", 30.0, "between 5 and 60"),
        ("max_booted", True, "simulator.max_booted must be a whole number between 1 and 8"),
        ("cursor_lead_ms", -1, "between 0 and 1000"),
        ("stream_max_width", 5000, "between 320 and 1600"),
        ("device_mode", "everyone", "simulator.device_mode must be one of: per_scope, shared"),
        ("stream_encoding", "vp9", "one of: auto, jpeg, h264"),
        ("connector", "adb", "simulator.connector must be one of: auto, idb, simctl"),
        ("companion_path", "bin/idb_companion", "simulator.companion_path must be an absolute path"),
        ("companion_path", "/a\n/b", "simulator.companion_path must be one line of at most 500 characters"),
        ("developer_dir", 3, "simulator.developer_dir must be one line"),
        ("device_type", "x" * 101, "simulator.device_type must be one line of at most 100 characters"),
        ("runtime", "iOS\x0026", "simulator.runtime must be one line"),
        ("device_name_prefix", "  ", "simulator.device_name_prefix must not be empty"),
        ("build_configuration", "", "simulator.build_configuration must be a build configuration name"),
        ("build_configuration", "Debug; rm -rf /", "build configuration name"),
        ("build_configuration", 7, "build configuration name"),
        ("server_host", "0.0.0.0", "simulator.server_host must be a loopback address: 127.0.0.1, localhost, ::1"),
        ("server_port", 80, "between 1024 and 65535"),
        ("allowed_origins", "http://localhost:3000", "must be a list of at most 20 origins"),
        ("allowed_origins", ["localhost:3000"], "origins, such as http://localhost:3000"),
        ("frame_ancestors", ["http://a.test"] * 21, "at most 20 origins"),
    ],
)
def test_a_bad_value_is_refused_with_what_would_do(key: str, value: Any, message: str) -> None:
    errors = schema.errors({key: value}, naming=host_naming, unknown="unknown simulator keys")
    assert len(errors) == 1 and message in errors[0]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("companion_path", ""),
        ("companion_path", "/opt/homebrew/bin/idb_companion"),
        ("developer_dir", "/Applications/Xcode-26.6.app/Contents/Developer"),
        ("device_type", "iPhone 17 Pro"),
        ("runtime", "iOS 26.5"),
        ("device_mode", "shared"),
        ("stream_encoding", "h264"),
        ("stream_fps", 60),
        ("cursor_lead_ms", 0),
        ("build_configuration", "Release"),
        ("build_configuration", "Beta-Staging 2"),
        ("server_host", "localhost"),
        ("allowed_origins", ("http://localhost:3000", "https://app.example.com", "http://[::1]:8000")),
        ("frame_ancestors", []),
    ],
)
def test_a_good_value_is_kept(key: str, value: Any) -> None:
    assert schema.errors({key: value}) == []


def test_unknown_keys_are_named_together_and_refusals_default_to_the_path() -> None:
    assert schema.errors({"enabled": True, "zoom": 2, "engine": "axe"}, unknown="unknown simulator keys") == [
        "unknown simulator keys: engine, zoom"
    ]
    assert schema.errors({"stream_fps": 1}) == ["stream.fps must be a whole number between 5 and 60"]


def test_a_setting_is_found_by_key_or_path_and_names_its_environment_variable() -> None:
    fps = schema.find("stream_fps")
    assert fps is not None and fps is schema.find("stream.fps") and schema.find("nope") is None
    assert fps.env == "SIM_MIRROR_STREAM_FPS"
    companion = schema.BY_KEY["companion_path"]
    assert companion.env == "SIM_MIRROR_CONNECTORS_IDB_COMPANION_PATH"
    assert schema.BY_KEY["enabled"].env == "SIM_MIRROR_ENABLED"
    assert repr(schema.SAME) == "the standalone default"


@pytest.mark.parametrize(
    ("rule", "raw", "value"),
    [
        (Flag(), " TRUE ", True),
        (Flag(), "off", False),
        (Whole(1, 8), " 3 ", 3),
        (Choice(("a", "b")), " a ", "a"),
        (AbsolutePath(), " /x ", "/x"),
        (Name(), " iPhone 17 ", "iPhone 17"),
        (ConfigurationName(), "Release", "Release"),
        (Origins(), "http://a.test, ,http://b.test:8080", ("http://a.test", "http://b.test:8080")),
        (Origins(), "", ()),
        (LoopbackHost(), "::1", "::1"),
    ],
)
def test_text_is_parsed_by_the_rule(rule: schema.Rule, raw: str, value: Any) -> None:
    assert rule.parse(raw) == value


def test_text_that_is_not_a_flag_or_a_number_is_refused_with_what_would_do() -> None:
    with pytest.raises(ValueError, match="must be true or false"):
        Flag().parse("maybe")
    with pytest.raises(ValueError, match="must be a whole number between 1 and 8"):
        Whole(1, 8).parse("three")


def test_every_rule_describes_what_it_allows() -> None:
    described = {type(setting.rule).__name__: setting.rule.describe() for setting in SETTINGS}
    assert described["Flag"] == "`true` or `false`"
    assert described["Whole"].startswith("a whole number from")
    assert described["Choice"].startswith("one of `")
    assert described["AbsolutePath"] == "an absolute path, or empty"
    assert described["ConfigurationName"].startswith("a build configuration name")
    assert "origins" in described["Origins"] and "`127.0.0.1`" in described["LoopbackHost"]
    assert Name().describe().endswith(", or empty") and not Name(required=True).describe().endswith("empty")


def test_flat_values_nest_as_config_toml_does_and_flatten_back() -> None:
    values = {"enabled": False, "stream_fps": 60, "companion_path": "/c", "allowed_origins": ("http://a.test",)}
    document = schema.nested(values)
    assert document == {
        "enabled": False,
        "stream": {"fps": 60},
        "connectors": {"idb": {"companion_path": "/c"}},
        "security": {"allowed_origins": ["http://a.test"]},
    }
    assert schema.flatten(document) == (values, [])


def test_flatten_names_what_is_no_setting_and_skips_what_it_is_told_to() -> None:
    document = {
        "stream": {"fps": 24, "zoom": 2},
        "connectors": {"idb": {"extra": 1}},
        "colour": "red",
        "scopes": {"demo": {"enabled": False}},
        "enabled": {"not": "a table setting"},
    }
    values, unknown = schema.flatten(document, skip=frozenset({"scopes"}))
    assert values == {"stream_fps": 24, "enabled": {"not": "a table setting"}}
    assert unknown == ["stream.zoom", "connectors.idb.extra", "colour"]
    assert schema.flatten({"scopes": {"x": 1}})[1] == ["scopes"]
