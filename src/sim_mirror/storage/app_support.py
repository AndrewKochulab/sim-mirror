# SPDX-License-Identifier: Apache-2.0
"""A standalone install's folders, and the `StateStore` over them.

* state -- ``~/Library/Application Support/SimMirror``: config.toml, remembered devices, builds, tokens;
* run -- ``~/.sim-mirror/run``: companion sockets and pid files. Short, because a unix socket's path may have only 104
  bytes, and Application Support's often does not leave room;
* logs -- ``~/Library/Logs/SimMirror``, where Console finds them;
* claims -- ``<state>/claims``: which process is using which device, shared by every host on the Mac.

Each moves with its environment variable (``SIM_MIRROR_STATE_DIR``, ``SIM_MIRROR_RUN_DIR``, ``SIM_MIRROR_LOG_DIR``,
``SIM_MIRROR_CLAIMS_DIR``), which is how tests and hosts keep their own.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from sim_mirror.scope import Scope
from sim_mirror.storage.private import ensure_private_dir

APP_NAME = "SimMirror"
#: Names a standalone install in pid files and device claims, and in the message another process shows.
OWNER_TAG = "SimMirror"
STATE_DIR_ENV = "SIM_MIRROR_STATE_DIR"
RUN_DIR_ENV = "SIM_MIRROR_RUN_DIR"
LOG_DIR_ENV = "SIM_MIRROR_LOG_DIR"
CLAIMS_DIR_ENV = "SIM_MIRROR_CLAIMS_DIR"
DEVICES_FILE = "devices.json"


def _from_env(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    raw = env.get(name, "").strip()
    return Path(raw).expanduser() if raw else fallback


def _home(home: Path | None) -> Path:
    return home if home is not None else Path.home()


def state_dir(env: Mapping[str, str], home: Path | None = None) -> Path:
    return _from_env(env, STATE_DIR_ENV, _home(home) / "Library" / "Application Support" / APP_NAME)


def run_dir(env: Mapping[str, str], home: Path | None = None) -> Path:
    return _from_env(env, RUN_DIR_ENV, _home(home) / ".sim-mirror" / "run")


def log_dir(env: Mapping[str, str], home: Path | None = None) -> Path:
    return _from_env(env, LOG_DIR_ENV, _home(home) / "Library" / "Logs" / APP_NAME)


def claims_dir(env: Mapping[str, str], home: Path | None = None) -> Path:
    """Where device claims are kept. A host embedding SimMirror should use this too, so hosts see each other."""
    return _from_env(env, CLAIMS_DIR_ENV, state_dir(env, home) / "claims")


def derived_data_root(home: Path | None = None) -> Path:
    return _home(home) / "Library" / "Developer" / "Xcode" / "DerivedData"


def file_name(scope_id: str) -> str:
    """A scope id as a file name: the colons a host's ids may have read as slashes in Finder."""
    return scope_id.replace(":", "_")


class AppSupportStateStore:
    owner_tag = OWNER_TAG

    def __init__(self, env: Mapping[str, str] | None = None, home: Path | None = None) -> None:
        self._env = dict(os.environ if env is None else env)
        self._home = home

    @property
    def state_dir(self) -> Path:
        return state_dir(self._env, self._home)

    def devices_file(self, scope: Scope) -> Path:
        return self.state_dir / DEVICES_FILE

    def builds_dir(self, scope: Scope) -> Path:
        return self.state_dir / "builds" / file_name(scope.id)

    def derived_data(self, scope: Scope) -> Path:
        return derived_data_root(self._home) / f"{APP_NAME}-{file_name(scope.id)}"

    def run_dir(self) -> Path:
        return run_dir(self._env, self._home)

    def log_dir(self) -> Path:
        return log_dir(self._env, self._home)

    def claims_dir(self) -> Path:
        return claims_dir(self._env, self._home)

    def ensure_dir(self, folder: Path) -> Path:
        return ensure_private_dir(folder)
