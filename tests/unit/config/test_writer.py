# SPDX-License-Identifier: Apache-2.0
"""sim-mirror config set/unset: one value changed, the person's file otherwise as it was, and a bad value refused
before anything is written."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.config.writer import ConfigError, ConfigWriter


def test_setting_values_makes_the_file_and_its_tables(tmp_path: Path) -> None:
    writer = ConfigWriter(tmp_path / "nested" / "config.toml")
    assert writer.set("stream.fps", "60") == 60
    assert writer.set("companion_path", "/opt/homebrew/bin/idb_companion") == "/opt/homebrew/bin/idb_companion"
    assert writer.set("security.allowed_origins", "http://localhost:3000") == ("http://localhost:3000",)
    assert writer.set("agent.cursor", "off", scope="ws:alpha:tp-1") is False
    text = writer.path.read_text()
    assert (
        "[stream]\nfps = 60" in text and '[connectors.idb]\ncompanion_path = "/opt/homebrew/bin/idb_companion"' in text
    )
    assert 'allowed_origins = ["http://localhost:3000"]' in text and '[scopes."ws:alpha:tp-1".agent]' in text
    assert writer.get("stream_fps") == 60 and writer.get("agent_cursor", scope="ws:alpha:tp-1") is False
    assert writer.get("stream.quality") is None and writer.get("agent_cursor", scope="other") is None
    assert writer.values() == {
        "stream_fps": 60,
        "companion_path": "/opt/homebrew/bin/idb_companion",
        "allowed_origins": ("http://localhost:3000",),
    }
    assert writer.values(scope="ws:alpha:tp-1") == {"agent_cursor": False}
    assert writer.values(scope="missing") == {}


def test_a_persons_comments_and_order_survive(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# my setup\nenabled = true  # keep on\n\n[stream]\n# smoother\nfps = 30\n")
    ConfigWriter(path).set("stream.fps", "60")
    assert path.read_text() == "# my setup\nenabled = true  # keep on\n\n[stream]\n# smoother\nfps = 60\n"


def test_unsetting_removes_the_value_and_the_tables_it_leaves_empty(tmp_path: Path) -> None:
    writer = ConfigWriter(tmp_path / "config.toml")
    writer.set("stream.fps", "60")
    writer.set("stream.quality", "80")
    writer.set("agent.cursor", "false", scope="demo")
    assert writer.unset("stream_fps") is True and "fps" not in writer.path.read_text()
    assert writer.unset("stream_fps") is False
    assert writer.unset("agent_cursor", scope="demo") is True
    assert "scopes" not in writer.path.read_text() and "[stream]" in writer.path.read_text()
    assert writer.unset("build.tools") is False and writer.unset("agent.cursor", scope="demo") is False


@pytest.mark.parametrize(
    ("name", "raw", "scope", "says"),
    [
        ("zoom", "2", None, "zoom is not a setting; `sim-mirror config list` shows them all"),
        ("stream.fps", "fast", None, "stream.fps must be a whole number between 5 and 60"),
        ("stream.fps", "200", None, "stream.fps must be a whole number between 5 and 60"),
        ("enabled", "maybe", None, "enabled must be true or false"),
        ("enabled", "true", "bad id", "'bad id' is not a scope id"),
    ],
)
def test_a_bad_change_is_refused_and_nothing_is_written(
    tmp_path: Path, name: str, raw: str, scope: str | None, says: str
) -> None:
    writer = ConfigWriter(tmp_path / "config.toml")
    with pytest.raises(ConfigError) as refused:
        writer.set(name, raw, scope=scope)
    assert str(refused.value) == says and not writer.path.exists()


def test_a_file_that_cannot_be_read_or_edited_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("enabled = \n")
    with pytest.raises(ConfigError, match="which is not valid TOML"):
        ConfigWriter(broken).set("enabled", "true")
    folder = tmp_path / "folder.toml"
    folder.mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        ConfigWriter(folder).get("enabled")
    clash = tmp_path / "clash.toml"
    clash.write_text("stream = 3\n")
    with pytest.raises(ConfigError, match="stream is a value, not a table"):
        ConfigWriter(clash).set("stream.fps", "30")
    assert ConfigWriter(clash).unset("stream.fps") is False
