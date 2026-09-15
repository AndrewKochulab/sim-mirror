# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror version``: the package, the screen protocol, and the connectors installed."""

from __future__ import annotations

import argparse
import importlib.metadata
from collections.abc import Callable, Iterable
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.registry import ENTRY_POINT_GROUP, builtin_factories
from sim_mirror.protocol import PROTOCOL_VERSION


def register(commands: Any) -> None:
    command = commands.add_parser("version", help="print the package, protocol and connector versions")
    command.set_defaults(handler=run)


def connector_names(entry_points: Callable[..., Iterable[Any]] = importlib.metadata.entry_points) -> list[str]:
    """The built-in connectors and every installed one, by name."""
    return sorted({*builtin_factories(), *(entry.name for entry in entry_points(group=ENTRY_POINT_GROUP))})


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    ctx.say(f"sim-mirror {__version__}")
    ctx.say(f"protocol v{PROTOCOL_VERSION}")
    ctx.say(f"connectors: {', '.join(connector_names())}")
    return 0
