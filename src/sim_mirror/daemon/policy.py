# SPDX-License-Identifier: Apache-2.0
"""What a standalone scope may do: the daemon's `Policy`.

There is no host application to turn an area off, so the simulator's own settings decide. An agent may install an app
built inside a folder its token names (``sim-mirror mcp --root DIR``), the scope's DerivedData, or Xcode's; it builds
in the first folder its token names. A build runs commands, which a standalone scope allows exactly while its build
tools are on -- the person who switched them on is the person whose machine it is.
"""

from __future__ import annotations

from pathlib import Path

from sim_mirror.build.xcodebuild import DERIVED_DATA
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource, StateStore


class ConfigPolicy:
    def __init__(self, config: ConfigSource, tokens: TokenStore, state: StateStore) -> None:
        self._config = config
        self._tokens = tokens
        self._state = state

    def area_enabled(self, scope: Scope) -> bool:
        return True

    def shells_allowed(self, scope: Scope) -> bool:
        return self._config.get(scope).build_tools

    def install_roots(self, scope: Scope) -> tuple[Path, ...]:
        return (*self._tokens.roots(scope.id), self._state.derived_data(scope), DERIVED_DATA)

    def build_folder(self, scope: Scope) -> Path | None:
        roots = self._tokens.roots(scope.id)
        return roots[0] if roots else None
