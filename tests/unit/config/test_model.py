# SPDX-License-Identifier: Apache-2.0
"""SimConfig: one attribute per setting, always whole and valid, a refused value reading as its default."""

from __future__ import annotations

from dataclasses import fields

from sim_mirror.config import schema
from sim_mirror.config.model import SimConfig


def test_it_has_exactly_one_attribute_per_setting() -> None:
    assert [field.name for field in fields(SimConfig)] == [setting.key for setting in schema.SETTINGS]


def test_the_defaults_are_the_profiles_own() -> None:
    assert SimConfig.defaults().to_flat() == schema.defaults("standalone")
    embedded = SimConfig.defaults("embedded")
    assert embedded.enabled is False and embedded.build_tools is True and embedded.server_port == 7466


def test_good_values_are_kept_refused_ones_read_as_defaults_and_strangers_are_ignored() -> None:
    config = SimConfig.from_flat(
        {"stream_fps": 999, "runtime": "iOS 26.5", "build_configuration": "-bad", "retired": 1,
         "allowed_origins": ["http://localhost:3000"], "device_mode": "shared"}
    )  # fmt: skip
    assert config.stream_fps == 30 and config.runtime == "iOS 26.5" and config.build_configuration == "Debug"
    assert config.allowed_origins == ("http://localhost:3000",) and config.device_mode == "shared"


def test_changing_values_checks_them_too() -> None:
    config = SimConfig.defaults().with_values(stream_fps=60, max_booted=99)
    assert config.stream_fps == 60 and config.max_booted == 2
    assert SimConfig.from_flat(config.to_flat()) == config
