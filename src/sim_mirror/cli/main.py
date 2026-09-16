# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror``: mirror and drive the iOS Simulator from any agent or browser.

Each command is a module that registers its arguments and runs with a `CliContext`. A refusal a person can act on --
a setting, a scope, a token, the daemon -- is one line on stderr and exit status 1, never a traceback.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sim_mirror._version import __version__
from sim_mirror.cli import (
    config_command,
    devices,
    doctor,
    mcp,
    open_viewer,
    serve,
    settings_command,
    tokens,
    tools,
    version,
    xcode,
)
from sim_mirror.cli.context import CliContext
from sim_mirror.config.writer import ConfigError
from sim_mirror.daemon.tokens import TokenRefused
from sim_mirror.mcp.launcher import DaemonUnavailable
from sim_mirror.scope import InvalidScope

COMMANDS = (serve, mcp, open_viewer, doctor, config_command, settings_command, devices, tokens, tools, xcode, version)
REFUSALS = (ConfigError, DaemonUnavailable, InvalidScope, TokenRefused)


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(
        prog="sim-mirror",
        description="Mirror and drive the iOS Simulator from Claude Code, CLI agents and the browser.",
    )
    made.add_argument("--version", action="version", version=f"sim-mirror {__version__}")
    commands = made.add_subparsers(dest="command", metavar="COMMAND")
    for command in COMMANDS:
        command.register(commands)
    return made


def main(argv: Sequence[str] | None = None, *, ctx: CliContext | None = None) -> int:
    context = ctx or CliContext.from_process()
    made = parser()
    args = made.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        made.print_help(context.stdout)
        return 0
    try:
        return int(handler(args, context))
    except REFUSALS as exc:
        context.complain(f"sim-mirror: {exc}")
        return 1
