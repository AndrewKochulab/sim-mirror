# SPDX-License-Identifier: Apache-2.0
"""A standalone install's settings store: where values come from is config.toml's layers, and a change is written to
it -- for one scope or every scope -- with a file it cannot edit refused like any other change."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.provenance import SettingOrigin
from sim_mirror.config.settings_store import FILE, TomlSettingsStore
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.config.writer import ConfigRefused
from sim_mirror.scope import Scope
from sim_mirror.seams import SettingsRefused

DEMO = Scope.named("demo")


def test_a_change_is_written_where_the_source_reads_it_and_explained_from_there(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    source = TomlConfigSource(path, env={"SIM_MIRROR_AGENT_CURSOR": "false"})
    store = TomlSettingsStore(source)
    store.change(DEMO, {"stream.fps": 12}, [])
    store.change(None, {"device.idle_minutes": 30}, [])
    assert source.get(DEMO).stream_fps == 12 and source.get(Scope.named("other")).stream_fps == 30
    origins = store.explain(DEMO)
    assert origins["stream_fps"].layer == "scope" and origins["idle_minutes"] == SettingOrigin("file", str(path))
    assert origins["agent_cursor"] == SettingOrigin("environment", "SIM_MIRROR_AGENT_CURSOR")
    store.change(DEMO, {}, ["stream.fps"])
    assert source.get(DEMO).stream_fps == 30


def test_a_refused_change_and_a_file_that_cannot_be_edited_are_both_settings_refusals(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    store = TomlSettingsStore(TomlConfigSource(path, env={}))
    with pytest.raises(ConfigRefused) as refused:
        store.change(DEMO, {"server.port": 7481}, [])
    assert isinstance(refused.value, SettingsRefused) and list(refused.value.errors) == ["server.port"]
    path.write_text("enabled = \n")
    with pytest.raises(SettingsRefused) as broken:
        store.change(None, {"stream.fps": 12}, [])
    assert list(broken.value.errors) == [FILE] and "not valid TOML" in broken.value.errors[FILE]
    assert str(SettingsRefused({})) == "the change was refused"
