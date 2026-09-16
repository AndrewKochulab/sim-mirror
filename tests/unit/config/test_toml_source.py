# SPDX-License-Identifier: Apache-2.0
"""config.toml as a ConfigSource: layers in their order, each value checked, read again when the file changes, and
what is wrong collected rather than fatal."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config import schema, toml_source
from sim_mirror.config.discovery import config_path
from sim_mirror.config.provenance import LAYERS, SettingOrigin
from sim_mirror.config.toml_source import TomlConfigSource
from sim_mirror.scope import Scope

DEMO = Scope.named("demo")


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_no_file_is_the_profiles_defaults(tmp_path: Path) -> None:
    source = TomlConfigSource(tmp_path / "config.toml", env={})
    assert source.get(DEMO).enabled is True and source.problems() == []
    assert source.path == tmp_path / "config.toml"
    assert TomlConfigSource(tmp_path / "none.toml", env={}, profile="embedded").get(DEMO).enabled is False


def test_layers_apply_lowest_first_and_a_refused_value_reads_as_the_layer_below(tmp_path: Path) -> None:
    path = write(
        tmp_path / "config.toml",
        """
# comments are fine
enabled = true
[stream]
fps = 24
quality = 50
[device]
max_booted = 3
[scopes."demo"]
device.max_booted = 5
stream.quality = 90
""",
    )
    source = TomlConfigSource(
        path,
        env={"SIM_MIRROR_STREAM_FPS": "48", "SIM_MIRROR_STREAM_QUALITY": "999"},
        overrides={"stream_fps": 999, "server_port": 7481},
    )
    demo = source.get(DEMO)
    assert (demo.stream_fps, demo.stream_quality, demo.max_booted, demo.server_port) == (48, 90, 5, 7481)
    other = source.get(Scope.named("other"))
    assert (other.stream_quality, other.max_booted) == (50, 3)
    assert source.problems() == [
        "SIM_MIRROR_STREAM_QUALITY must be a whole number between 30 and 100",
        "stream.fps must be a whole number between 5 and 60",
    ]


def test_each_value_says_which_layer_set_it_and_a_refused_one_names_the_layer_below(tmp_path: Path) -> None:
    path = write(
        tmp_path / "config.toml",
        '[stream]\nfps = 24\nquality = 50\nmax_width = 99999\n[scopes."demo"]\nstream.quality = 90\n',
    )
    source = TomlConfigSource(
        path,
        env={"SIM_MIRROR_STREAM_FPS": "48", "SIM_MIRROR_AGENT_CURSOR_LEAD_MS": "5000"},
        overrides={"server_port": 7481, "agent_cursor": "not a flag"},
    )
    origins = source.explain(DEMO)
    assert origins["stream_fps"] == SettingOrigin("environment", "SIM_MIRROR_STREAM_FPS")
    assert origins["stream_quality"] == SettingOrigin("scope", f'[scopes."demo"] in {path}')
    assert origins["server_port"] == SettingOrigin("command_line")
    assert origins["enabled"] == SettingOrigin("default")
    # Refused, each reads as the layer below it would: the file's 24 is replaced, these are not.
    assert origins["stream_max_width"] == SettingOrigin("default")
    assert origins["cursor_lead_ms"] == SettingOrigin("default")
    assert origins["agent_cursor"] == SettingOrigin("default")
    assert source.explain(Scope.named("other"))["stream_quality"] == SettingOrigin("file", str(path))
    assert set(origins) == {setting.key for setting in schema.SETTINGS}


def test_only_a_value_config_toml_holds_can_be_changed_by_writing_it() -> None:
    assert SettingOrigin("default").writable and SettingOrigin("file", "/c.toml").writable
    assert SettingOrigin("scope", '[scopes."demo"]').writable
    assert not SettingOrigin("environment", "SIM_MIRROR_STREAM_FPS").writable
    assert not SettingOrigin("command_line").writable
    labels = [SettingOrigin(layer).label() for layer in LAYERS]
    assert labels == ["default", "file", "scope", "environment", "command line"]
    assert SettingOrigin("environment", "SIM_MIRROR_STREAM_FPS").label() == "SIM_MIRROR_STREAM_FPS"


def test_the_file_is_read_again_only_when_it_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path / "config.toml", "[stream]\nfps = 24\n")
    source = TomlConfigSource(path, env={})
    reads: list[Path] = []
    real = toml_source.read_document

    def counted(file: Path, stamp: toml_source.Stamp | None) -> toml_source.Document:
        reads.append(file)
        return real(file, stamp)

    monkeypatch.setattr(toml_source, "read_document", counted)
    assert source.get(DEMO).stream_fps == 24 and source.get(DEMO).stream_fps == 24
    assert len(reads) == 1
    write(path, "[stream]\nfps = 60 # now faster\n")
    assert source.get(DEMO).stream_fps == 60 and len(reads) == 2
    path.unlink()
    assert source.get(DEMO).stream_fps == 30


def test_what_is_wrong_with_the_file_is_collected_and_the_rest_still_read(tmp_path: Path) -> None:
    path = write(
        tmp_path / "config.toml",
        """
colour = "red"
[stream]
fps = 1
max_width = 600
[scopes]
"bad id" = { enabled = false }
lonely = 3
[scopes.demo]
zoom = 2
stream.fps = 0
""",
    )
    source = TomlConfigSource(path, env={}, overrides={"zoom": 1})
    assert source.get(DEMO).stream_max_width == 600
    problems = [problem.removeprefix(f"{path}: ") for problem in source.problems()]
    assert problems == [
        "colour is not a setting",
        "stream.fps must be a whole number between 5 and 60",
        "scopes.bad id must be a table named by a scope id",
        "scopes.lonely must be a table named by a scope id",
        "scopes.demo.zoom is not a setting",
        "scopes.demo: stream.fps must be a whole number between 5 and 60",
        "unknown command-line settings: zoom",
    ]


@pytest.mark.parametrize("text", ["enabled = ", "scopes = 3\n"])
def test_a_file_that_is_not_toml_or_has_a_bad_scopes_value_is_reported(tmp_path: Path, text: str) -> None:
    path = write(tmp_path / "config.toml", text)
    source = TomlConfigSource(path, env={})
    assert source.get(DEMO).enabled is True
    assert len(source.problems()) == 1 and str(path) in source.problems()[0]


def test_an_unreadable_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe")
    assert "config.toml" in TomlConfigSource(path, env={}).problems()[0]


def test_the_file_is_found_through_the_environment_or_in_application_support(tmp_path: Path) -> None:
    assert config_path({"SIM_MIRROR_CONFIG": "~/x.toml"}, home=tmp_path).name == "x.toml"
    assert config_path({}, home=tmp_path) == tmp_path / "Library" / "Application Support" / "SimMirror" / "config.toml"
    assert config_path({"SIM_MIRROR_STATE_DIR": str(tmp_path / "s")}) == tmp_path / "s" / "config.toml"
    assert config_path().name == "config.toml"
