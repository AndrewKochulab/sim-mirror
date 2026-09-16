# SPDX-License-Identifier: Apache-2.0
"""sim-mirror config set/unset: one value changed, the person's file otherwise as it was, and a bad value refused
before anything is written."""

from __future__ import annotations

import stat
import threading
from pathlib import Path

import pytest

from sim_mirror.config.writer import Changed, ConfigError, ConfigRefused, ConfigWriter


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


def test_a_change_sets_and_removes_several_at_once_and_says_what_it_did(tmp_path: Path) -> None:
    writer = ConfigWriter(tmp_path / "config.toml")
    writer.set("stream.quality", "80")
    changed = writer.change({"stream_fps": 24, "security.allowed_origins": ["http://localhost:3000"]},
                            unset=["stream.quality", "agent.cursor"])  # fmt: skip
    assert changed == Changed({"stream.fps": 24, "security.allowed_origins": ("http://localhost:3000",)},
                              ("stream.quality",))  # fmt: skip
    assert writer.values() == {"stream_fps": 24, "allowed_origins": ("http://localhost:3000",)}
    nothing = ConfigWriter(tmp_path / "untouched.toml")
    assert nothing.change(unset=["stream.fps"]) == Changed({}, ()) and not nothing.path.exists()


def test_a_change_with_anything_wrong_writes_nothing_and_names_each_problem(tmp_path: Path) -> None:
    writer = ConfigWriter(tmp_path / "config.toml")
    with pytest.raises(ConfigRefused) as refused:
        writer.change({"stream.fps": 24, "stream.quality": 5, "zoom": 2}, unset=["colour"])
    assert refused.value.errors == {
        "stream.quality": "stream.quality must be a whole number between 30 and 100",
        "zoom": "zoom is not a setting",
        "colour": "colour is not a setting",
    }
    assert str(refused.value) == "stream.quality must be a whole number between 30 and 100"
    assert not writer.path.exists()


def test_a_setting_only_the_whole_daemon_reads_is_refused_for_a_scope_both_ways(tmp_path: Path) -> None:
    writer = ConfigWriter(tmp_path / "config.toml")
    says = "server.port applies to the whole daemon, so it cannot be set for one scope; set it without a scope"
    with pytest.raises(ConfigRefused, match="applies to the whole daemon") as setting:
        writer.set("server.port", "7481", scope="demo")
    with pytest.raises(ConfigRefused) as removing:
        writer.change(unset=["server_port"], scope="demo")
    assert setting.value.errors == removing.value.errors == {"server.port": says}
    assert writer.set("server.port", "7481") == 7481 and writer.set("stream.fps", "24", scope="demo") == 24


def test_a_persons_file_keeps_its_mode_and_its_folder_while_a_new_one_is_the_owners_only(tmp_path: Path) -> None:
    folder = tmp_path / "mine"
    folder.mkdir(mode=0o755)
    kept = folder / "config.toml"
    kept.write_text("enabled = true\n")
    kept.chmod(0o644)
    ConfigWriter(kept).set("stream.fps", "24")
    assert stat.S_IMODE(kept.stat().st_mode) == 0o644 and stat.S_IMODE(folder.stat().st_mode) == 0o755
    made = ConfigWriter(tmp_path / "new" / "config.toml")
    made.set("stream.fps", "24")
    assert stat.S_IMODE(made.path.stat().st_mode) == 0o600
    assert sorted(path.name for path in folder.iterdir()) == [".config.toml.lock", "config.toml"]


def test_two_changes_at_once_each_keep_the_others_value(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    start = threading.Barrier(8)

    def change(key: str, value: int) -> None:
        start.wait()
        ConfigWriter(path).change({key: value})

    keys = ["stream_fps", "stream_quality", "stream_max_width", "max_booted",
            "idle_minutes", "cursor_lead_ms", "screenshot_width", "snapshot_max_elements"]  # fmt: skip
    values = [24, 60, 800, 3, 30, 100, 300, 50]
    threads = [threading.Thread(target=change, args=pair) for pair in zip(keys, values, strict=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert ConfigWriter(path).values() == dict(zip(keys, values, strict=True))


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
