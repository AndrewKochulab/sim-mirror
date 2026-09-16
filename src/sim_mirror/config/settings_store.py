# SPDX-License-Identifier: Apache-2.0
"""A standalone install's `SettingsStore`: where each value comes from is `TomlConfigSource.explain`, and a change is
`ConfigWriter.change` on the same config.toml."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from sim_mirror.config.provenance import SettingOrigin
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.config.writer import ConfigError, ConfigRefused, ConfigWriter
from sim_mirror.scope import Scope
from sim_mirror.seams import SettingsRefused

#: What a refusal about the file itself -- not any one setting -- is filed under.
FILE = "config.toml"


class TomlSettingsStore:
    def __init__(self, source: TomlConfigSource, writer: ConfigWriter | None = None) -> None:
        self._source = source
        self._writer = writer or ConfigWriter(source.path)

    def explain(self, scope: Scope) -> Mapping[str, SettingOrigin]:
        return self._source.explain(scope)

    def change(self, scope: Scope | None, values: Mapping[str, Any], removed: Collection[str]) -> None:
        try:
            self._writer.change(values, removed, scope=None if scope is None else scope.id)
        except ConfigRefused:
            raise
        except ConfigError as exc:
            raise SettingsRefused({FILE: str(exc)}) from exc
