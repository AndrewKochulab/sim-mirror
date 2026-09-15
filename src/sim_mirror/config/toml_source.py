# SPDX-License-Identifier: Apache-2.0
"""A standalone install's `ConfigSource`: config.toml, the environment and command-line values, read as they change.

Precedence, lowest first: the profile's defaults, the file's values, the file's ``[scopes."<id>"]`` table for the scope
asked about, ``SIM_MIRROR_*`` variables, and values given on the command line. Every value is checked by its rule, and
one that is refused reads as the next lower value would.

The file is read again only when its modification time or size changes, so `get` can be called on every operation
and a `sim-mirror config set` applies to the next one. What is wrong with the file or the variables is collected by
`problems` for `sim-mirror config validate` and `sim-mirror doctor`, and never stops the rest from being read.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.config import schema
from sim_mirror.config.env import from_env
from sim_mirror.config.model import SimConfig
from sim_mirror.config.schema import Profile
from sim_mirror.scope import ID_PATTERN, Scope

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the Python 3.10 branch; CI runs it
    import tomli as tomllib

SCOPES_TABLE = "scopes"

Stamp = tuple[int, int]


@dataclass(frozen=True)
class Document:
    """What one version of the file says."""

    stamp: Stamp | None
    values: dict[str, Any]
    scopes: dict[str, dict[str, Any]]
    problems: tuple[str, ...]


def read_document(path: Path, stamp: Stamp | None) -> Document:
    """The file's values, its per-scope values and what is wrong with it. A missing file is an empty one."""
    if stamp is None:
        return Document(None, {}, {}, ())
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return Document(stamp, {}, {}, (f"{path}: {exc}",))
    values, unknown = schema.flatten(document, skip=frozenset({SCOPES_TABLE}))
    problems = [f"{path}: {name} is not a setting" for name in unknown]
    problems += [f"{path}: {message}" for message in schema.errors(values)]
    scopes: dict[str, dict[str, Any]] = {}
    table = document.get(SCOPES_TABLE, {})
    if not isinstance(table, dict):
        problems.append(f"{path}: {SCOPES_TABLE} must be a table of scope ids")
        table = {}
    for scope_id, overrides in table.items():
        if not isinstance(overrides, dict) or not ID_PATTERN.match(scope_id):
            problems.append(f"{path}: {SCOPES_TABLE}.{scope_id} must be a table named by a scope id")
            continue
        scoped, strangers = schema.flatten(overrides)
        problems += [f"{path}: {SCOPES_TABLE}.{scope_id}.{name} is not a setting" for name in strangers]
        problems += [f"{path}: {SCOPES_TABLE}.{scope_id}: {message}" for message in schema.errors(scoped)]
        scopes[scope_id] = scoped
    return Document(stamp, values, scopes, tuple(problems))


class TomlConfigSource:
    def __init__(
        self,
        path: Path,
        *,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
        profile: Profile = "standalone",
    ) -> None:
        self._path = path
        self._env_values, self._env_problems = from_env(os.environ if env is None else env)
        self._overrides = dict(overrides or {})
        self._profile: Profile = profile
        self._document: Document | None = None

    @property
    def path(self) -> Path:
        return self._path

    def get(self, scope: Scope) -> SimConfig:
        document = self._read()
        layers = (document.values, document.scopes.get(scope.id, {}), self._env_values, self._overrides)
        config = SimConfig.defaults(self._profile)
        for layer in layers:
            config = config.overlay(layer)
        return config

    def problems(self) -> list[str]:
        refused = schema.errors(self._overrides, unknown="unknown command-line settings")
        return [*self._read().problems, *self._env_problems, *refused]

    def _stamp(self) -> Stamp | None:
        try:
            status = self._path.stat()
        except OSError:
            return None
        return status.st_mtime_ns, status.st_size

    def _read(self) -> Document:
        stamp = self._stamp()
        if self._document is None or self._document.stamp != stamp:
            self._document = read_document(self._path, stamp)
        return self._document
