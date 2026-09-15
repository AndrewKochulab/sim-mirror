# SPDX-License-Identifier: Apache-2.0
"""Changing config.toml the way `sim-mirror config set` and `unset` do: one value, the rest of the file as it was.

config.toml is a person's file, so it is edited in place with tomlkit -- comments, order and blank lines survive --
and written whole or not at all. A value is parsed by its setting's rule from the text given on the command line and
refused, with the rule's message, before anything is written. ``scope`` edits that scope's ``[scopes."<id>"]`` table.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast

import tomlkit
from tomlkit.exceptions import TOMLKitError

from sim_mirror.config import schema
from sim_mirror.config.schema import Setting
from sim_mirror.scope import ID_PATTERN

SCOPES_TABLE = "scopes"

Table = MutableMapping[str, Any]


class ConfigError(Exception):
    """A change to config.toml that cannot be made, said so a person can fix it."""


def _setting(name: str) -> Setting:
    setting = schema.find(name)
    if setting is None:
        raise ConfigError(f"{name} is not a setting; `sim-mirror config list` shows them all")
    return setting


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

    def _save(self, document: tomlkit.TOMLDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
        os.replace(temporary, self._path)

    @staticmethod
    def _tables(setting: Setting, scope: str | None) -> tuple[list[str], str]:
        if scope is not None and not ID_PATTERN.match(scope):
            raise ConfigError(f"{scope!r} is not a scope id")
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
        tables, leaf = self._tables(setting, scope)
        node = self._walk(self._load(), tables, create=False)
        if node is None or leaf not in node:
            return None
        value = node[leaf]
        return value.unwrap() if hasattr(value, "unwrap") else value

    def set(self, name: str, raw: str, *, scope: str | None = None) -> Any:
        """Set a setting from text, answering the value written. Refuses a value its rule does not allow."""
        setting = _setting(name)
        try:
            value = setting.rule.parse(raw)
        except ValueError as exc:
            raise ConfigError(f"{setting.path} {exc}") from exc
        refused = setting.errors(value)
        if refused:
            raise ConfigError(refused[0])
        document = self._load()
        tables, leaf = self._tables(setting, scope)
        node = self._walk(document, tables, create=True)
        assert node is not None
        node[leaf] = list(value) if isinstance(value, tuple) else value
        self._save(document)
        return value

    def unset(self, name: str, *, scope: str | None = None) -> bool:
        """Remove a setting from the file, and any table that leaves empty. Answers whether it was there."""
        setting = _setting(name)
        document = self._load()
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
        self._save(document)
        return True

    def values(self, *, scope: str | None = None) -> dict[str, Any]:
        """Every setting the file sets (for a scope, in its table), by flat key."""
        document = self._load().unwrap()
        if scope is None:
            found, _unknown = schema.flatten(document, skip=frozenset({SCOPES_TABLE}))
            return found
        scoped = (document.get(SCOPES_TABLE) or {}).get(scope)
        found, _unknown = schema.flatten(scoped if isinstance(scoped, dict) else {})
        return found
