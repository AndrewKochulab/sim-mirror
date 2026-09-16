# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror serve``: run the daemon on 127.0.0.1 -- in this process, or detached with its output in the log folder.

The admin token is made on the first start. While the daemon runs, ``daemon.json`` in the run folder names its pid and
port, so every other command finds it; a second daemon is refused while one is running.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.cli.context import DAEMON_LOG, CliContext
from sim_mirror.config.settings_store import TomlSettingsStore
from sim_mirror.core.devices import JsonDeviceMemory
from sim_mirror.daemon.app import SERVER_SCOPE, build_daemon, create_app
from sim_mirror.daemon.lifecycle import LOOPBACK, DaemonInfo, read_info, remove_info, write_info
from sim_mirror.mcp.launcher import ensure_daemon


def register(commands: Any) -> None:
    command = commands.add_parser("serve", help="run the SimMirror daemon on 127.0.0.1")
    command.add_argument("--port", type=int, help="the port to listen on, instead of server.port")
    command.add_argument("--config", help="the config.toml to read, instead of the usual one")
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--detach", action="store_true", help="start it in the background and return")
    mode.add_argument("--foreground", action="store_true", help="run it in this process (the default)")
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    source = ctx.config(args.config, {"server_port": args.port} if args.port else None)
    for problem in source.problems():
        ctx.complain(f"config: {problem}")
    settings = source.get(SERVER_SCOPE)
    state = ctx.state
    running = read_info(state.run_dir())
    if running is not None:
        ctx.complain(f"a SimMirror daemon is already running at {running.url} (pid {running.pid})")
        return 0 if args.detach else 1
    if args.detach:
        forwarded = [
            *(["--port", str(args.port)] if args.port else []),
            *(["--config", args.config] if args.config else []),
        ]
        client = ctx.client(f"http://{LOOPBACK}:{settings.server_port}")
        ensure_daemon(client, lambda: ctx.start_daemon(*forwarded))
        ctx.say(f"the SimMirror daemon is running at {client.url}; its log is {state.log_dir() / DAEMON_LOG}")
        return 0
    tokens = ctx.tokens()
    tokens.admin_token()
    daemon = build_daemon(
        config=source,
        state=state,
        memory=JsonDeviceMemory(state.devices_file()),
        tokens=tokens,
        port=settings.server_port,
        xcrun=ctx.xcrun,
        settings=TomlSettingsStore(source),
    )
    write_info(state.run_dir(), DaemonInfo(os.getpid(), settings.server_port, __version__))
    ctx.say(f"SimMirror {__version__} on http://{LOOPBACK}:{settings.server_port}")
    # Removed as soon as the app has shut down: uvicorn raises a SIGTERM it caught again once it has, which ends the
    # process before this function returns. The `finally` covers every other way out.
    app = create_app(daemon, on_stopped=lambda: remove_info(state.run_dir(), os.getpid()))
    try:
        asyncio.run(ctx.serve(app, settings.server_host, settings.server_port))
    finally:
        remove_info(state.run_dir(), os.getpid())
    return 0
