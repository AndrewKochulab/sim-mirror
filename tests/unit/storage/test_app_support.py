# SPDX-License-Identifier: Apache-2.0
"""A standalone install's folders: where macOS keeps an app's things, each movable by its variable."""

from __future__ import annotations

import stat
from pathlib import Path

from sim_mirror.scope import Scope
from sim_mirror.storage import app_support
from sim_mirror.storage.app_support import AppSupportStateStore

SCOPE = Scope(id="ws:alpha:tp-1", group="alpha", label="alpha")


def test_the_folders_are_where_macos_keeps_an_apps_things(tmp_path: Path) -> None:
    store = AppSupportStateStore(env={}, home=tmp_path)
    support = tmp_path / "Library" / "Application Support" / "SimMirror"
    assert store.state_dir == support
    assert store.devices_file(SCOPE) == support / "devices.json"
    assert store.builds_dir(SCOPE) == support / "builds" / "ws_alpha_tp-1"
    assert store.derived_data(SCOPE) == tmp_path / "Library/Developer/Xcode/DerivedData/SimMirror-ws_alpha_tp-1"
    assert store.run_dir() == tmp_path / ".sim-mirror" / "run"
    assert store.log_dir() == tmp_path / "Library" / "Logs" / "SimMirror"
    assert store.claims_dir() == support / "claims"
    assert store.owner_tag == "SimMirror"


def test_each_folder_moves_with_its_variable(tmp_path: Path) -> None:
    env = {
        app_support.STATE_DIR_ENV: str(tmp_path / "state"),
        app_support.RUN_DIR_ENV: str(tmp_path / "r"),
        app_support.LOG_DIR_ENV: str(tmp_path / "logs"),
    }
    store = AppSupportStateStore(env=env, home=tmp_path / "home")
    assert store.state_dir == tmp_path / "state" and store.claims_dir() == tmp_path / "state" / "claims"
    assert store.run_dir() == tmp_path / "r" and store.log_dir() == tmp_path / "logs"
    moved = AppSupportStateStore(env={**env, app_support.CLAIMS_DIR_ENV: " ~/claims "}, home=tmp_path)
    assert moved.claims_dir() == Path("~/claims").expanduser()


def test_the_store_reads_this_processs_environment_by_default_and_makes_private_folders(tmp_path: Path) -> None:
    store = AppSupportStateStore()
    assert store.run_dir().name == "run" and "sim-mirror" in str(store.run_dir())
    made = store.ensure_dir(tmp_path / "a" / "b")
    assert made.is_dir() and stat.S_IMODE(made.stat().st_mode) == 0o700
    assert app_support.derived_data_root().name == "DerivedData"
