# SPDX-License-Identifier: Apache-2.0
"""Changing config.toml the way `sim-mirror config set` and `unset`, and the viewer's settings panel, do.

config.toml is a person's file, so it is edited in place with tomlkit -- comments, order and blank lines survive -- and
written whole or not at all (`storage.private.write_atomic`), keeping the mode it had. A change is one or more values
set and settings removed, for the whole file or for one scope's ``[scopes."<id>"]`` table: every one is checked by its
setting's rule, and a setting only the whole daemon reads (`Setting.reach`) is refused for a scope, before anything is
written. The file is read again and written under a lock beside it, so two changes at once -- the command line and a
page -- each see the other's.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tomlkit
from tomlkit.exceptions import TOMLKitError

from sim_mirror.config import schema
from sim_mirror.config.schema import Setting
from sim_mirror.scope import ID_PATTERN
from sim_mirror.storage.private import file_lock, write_atomic

SCOPES_TABLE = "scopes"

Table = MutableMapping[str, Any]


class ConfigError(Exception):
    """A change to config.toml that cannot be made, said so a person can fix it."""


class ConfigRefused(ConfigError):
    """A change refused before anything was written, with what is wrong with each setting it names, by path."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__(next(iter(errors.values())))
        self.errors = dict(errors)


@dataclass(frozen=True)
class Changed:
    """What a change wrote: the values set, by path, and the paths removed that were there."""

    set: dict[str, Any]
    unset: tuple[str, ...]


def _setting(name: str) -> Setting:
    setting = schema.find(name)
    if setting is None:
        raise ConfigError(f"{name} is not a setting; `sim-mirror config list` shows them all")
    return setting


def whole_daemon_only(setting: Setting) -> str:
    return f"{setting.path} applies to the whole daemon, so it cannot be set for one scope; set it without a scope"


class ConfigWriter:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> tomlkit.TOMLDocument:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return tomlkit.document()
        except OSError as exc:
            raise ConfigError(f"cannot read {self._path}: {exc}") from exc
        try:
            return tomlkit.parse(text)
        except TOMLKitError as exc:
            raise ConfigError(f"cannot edit {self._path}, which is not valid TOML: {exc}") from exc

    def _lock(self) -> Any:
        return file_lock(self._path.with_name(f".{self._path.name}.lock"), private_folder=False)

    @staticmethod
    def _scoped(scope: str | None) -> None:
        if scope is not None and not ID_PATTERN.match(scope):
            raise ConfigError(f"{scope!r} is not a scope id")

    @staticmethod
    def _tables(setting: Setting, scope: str | None) -> tuple[list[str], str]:
        *tables, leaf = setting.path.split(".")
        return ([SCOPES_TABLE, scope, *tables] if scope is not None else tables), leaf

    @staticmethod
    def _walk(document: Table, tables: list[str], *, create: bool) -> Table | None:
        node = document
        for name in tables:
            child = node.get(name)
            if child is None:
                if not create:
                    return None
                child = tomlkit.table()
                node[name] = child
            if not isinstance(child, MutableMapping):
                raise ConfigError(f"cannot edit {'.'.join(tables)}: {name} is a value, not a table")
            node = cast(Table, child)
        return node

    def get(self, name: str, *, scope: str | None = None) -> Any:
        """The value the file sets, or None when it sets none."""
        setting = _setting(name)
        self._scoped(scope)
        tables, leaf = self._tables(setting, scope)
        node = self._walk(self._load(), tables, create=False)
        if node is None or leaf not in node:
            return None
        value = node[leaf]
        return value.unwrap() if hasattr(value, "unwrap") else value

    def change(
        self, set: Mapping[str, Any] | None = None, unset: Collection[str] = (), *, scope: str | None = None
    ) -> Changed:
        """Set values and remove settings -- by key or path -- all or none. Raises `ConfigRefused` naming each
        setting that cannot be changed, and `ConfigError` for a file that cannot be edited."""
        self._scoped(scope)
        errors: dict[str, str] = {}
        chosen: dict[str, tuple[Setting, Any]] = {}
        for name, raw in (set or {}).items():
            setting = schema.find(name)
            value = tuple(raw) if isinstance(raw, list) else raw
            refused = [] if setting is None else setting.errors(value)
            if setting is None:
                errors[name] = f"{name} is not a setting"
            elif scope is not None and setting.reach == "global":
                errors[setting.path] = whole_daemon_only(setting)
            elif refused:
                errors[setting.path] = refused[0]
            else:
                chosen[setting.path] = (setting, value)
        removed: list[Setting] = []
        for name in unset:
            setting = schema.find(name)
            if setting is None:
                errors[name] = f"{name} is not a setting"
            elif scope is not None and setting.reach == "global":
                errors[setting.path] = whole_daemon_only(setting)
            else:
                removed.append(setting)
        if errors:
            raise ConfigRefused(errors)
        with self._lock():
            document = self._load()
            for setting, value in chosen.values():
                tables, leaf = self._tables(setting, scope)
                node = self._walk(document, tables, create=True)
                assert node is not None
                node[leaf] = list(value) if isinstance(value, tuple) else value
            gone = tuple(setting.path for setting in removed if self._remove(document, setting, scope))
            if chosen or gone:
                write_atomic(self._path, tomlkit.dumps(document).encode("utf-8"))
        return Changed({path: value for path, (_setting_, value) in chosen.items()}, gone)

    def _remove(self, document: tomlkit.TOMLDocument, setting: Setting, scope: str | None) -> bool:
        """Remove a setting, and every table that leaves empty. Answers whether it was there."""
        tables, leaf = self._tables(setting, scope)
        path = [cast(Table, document)]
        for table in tables:
            child = path[-1].get(table)
            if not isinstance(child, MutableMapping):
                return False
            path.append(cast(Table, child))
        if leaf not in path[-1]:
            return False
        del path[-1][leaf]
        for depth in range(len(tables), 0, -1):
            if len(path[depth]) == 0:
                del path[depth - 1][tables[depth - 1]]
        return True

    def set(self, name: str, raw: str, *, scope: str | None = None) -> Any:
        """Set a setting from text, answering the value written. Refuses a value its rule does not allow."""
        setting = _setting(name)
        try:
            value = setting.rule.parse(raw)
        except ValueError as exc:
            raise ConfigError(f"{setting.path} {exc}") from exc
        return self.change({setting.path: value}, scope=scope).set[setting.path]

    def unset(self, name: str, *, scope: str | None = None) -> bool:
        """Remove a setting from the file, and any table that leaves empty. Answers whether it was there."""
        setting = _setting(name)
        return bool(self.change(unset=[setting.path], scope=scope).unset)

    def values(self, *, scope: str | None = None) -> dict[str, Any]:
        """Every setting the file sets (for a scope, in its table), by flat key."""
        document = self._load().unwrap()
        if scope is None:
            found, _unknown = schema.flatten(document, skip=frozenset({SCOPES_TABLE}))
            return found
        scoped = (document.get(SCOPES_TABLE) or {}).get(scope)
        found, _unknown = schema.flatten(scoped if isinstance(scoped, dict) else {})
        return found
