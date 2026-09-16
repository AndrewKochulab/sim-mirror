# SPDX-License-Identifier: Apache-2.0
"""A scope's settings as one typed, immutable value.

`SimConfig` has one attribute per setting in `schema.SETTINGS`, named by its flat key. It is always whole and always
valid: `from_flat` starts from a profile's defaults and keeps each given value its rule allows, so a refused value reads
as its default and never breaks the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any, Literal

from sim_mirror.config import schema
from sim_mirror.config.schema import Profile


@dataclass(frozen=True)
class SimConfig:
    enabled: bool
    #: ``auto``, or a connector's name.
    connector: str
    companion_path: str
    mcpbridge_merge: bool
    developer_dir: str
    device_type: str
    runtime: str
    device_mode: Literal["per_scope", "shared"]
    device_name_prefix: str
    max_booted: int
    idle_minutes: int
    shutdown_on_idle: bool
    device_typing: Literal["auto", "keys", "paste"]
    stream_encoding: Literal["auto", "jpeg", "h264"]
    stream_fps: int
    stream_quality: int
    stream_max_width: int
    agent_tools: bool
    agent_cursor: bool
    cursor_lead_ms: int
    screenshot_width: int
    snapshot_max_elements: int
    build_tools: bool
    build_configuration: str
    build_timeout_minutes: int
    build_test_diagnostics: bool
    server_host: str
    server_port: int
    allowed_origins: tuple[str, ...]
    frame_ancestors: tuple[str, ...]

    @classmethod
    def defaults(cls, profile: Profile = "standalone") -> SimConfig:
        return cls.from_flat({}, profile)

    @classmethod
    def from_flat(cls, values: Mapping[str, Any], profile: Profile = "standalone") -> SimConfig:
        """A profile's defaults, with every value in `values` its rule allows. Keys that are no setting are ignored."""
        return cls(**schema.defaults(profile)).overlay(values)

    def overlay(self, values: Mapping[str, Any]) -> SimConfig:
        """This config with every value in `values` its rule allows; a refused value leaves the current one."""
        chosen = self.to_flat()
        for setting in schema.SETTINGS:
            if setting.key not in values:
                continue
            value = values[setting.key]
            value = tuple(value) if isinstance(value, list) else value
            if not setting.errors(value):
                chosen[setting.key] = value
        return SimConfig(**chosen)

    def to_flat(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def with_values(self, **changes: Any) -> SimConfig:
        """This config with some values changed, each still checked by its rule."""
        return self.overlay(changes)
