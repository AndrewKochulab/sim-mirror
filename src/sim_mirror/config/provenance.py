# SPDX-License-Identifier: Apache-2.0
"""Where a setting's value comes from: which layer of the configuration set the value a scope sees.

A person changing a setting needs to know whether the change will stick. One set by an environment variable or on the
command line cannot be changed from config.toml -- the variable wins over the file -- so a page offering to change it
would write a value nothing reads. The layers, lowest first, are those `TomlConfigSource.get` applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Layer = Literal["default", "file", "scope", "environment", "command_line"]
LAYERS: tuple[Layer, ...] = ("default", "file", "scope", "environment", "command_line")
#: The layers config.toml is: a page may change a value set there, or not set at all.
WRITABLE: frozenset[Layer] = frozenset({"default", "file", "scope"})


@dataclass(frozen=True)
class SettingOrigin:
    """The layer a value comes from, and what in it: the file, the scope's table, or the variable."""

    layer: Layer
    detail: str | None = None

    @property
    def writable(self) -> bool:
        """Whether writing config.toml changes what is read: not while a variable or the command line sets it."""
        return self.layer in WRITABLE

    def label(self) -> str:
        """How `sim-mirror config list` names it."""
        if self.layer == "command_line":
            return "command line"
        return self.detail or self.layer


DEFAULT = SettingOrigin("default")
