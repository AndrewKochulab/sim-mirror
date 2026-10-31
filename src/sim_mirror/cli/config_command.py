# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror config``: where the settings file is, what it sets, and changing it one value at a time.

``set`` and ``unset`` edit config.toml in place (`config.writer`) -- comments survive -- refusing a value its rule does
not allow before anything is written. A running daemon is told to reload, so a switch turned off is off before the
command returns. ``--scope`` edits that scope's own table.

``copy --from A --scope B`` copies what scope A's own table sets into B's, keeping each value B sets already: how a
git worktree's scope gets its main checkout's settings, as `sim-mirror doctor` suggests (`doctor.worktree`).

``scope`` prints the scope id a folder has -- the current one unless ``--folder`` names another -- and nothing else,
so a script can pass it on: ``sim-mirror config set --scope "$(sim-mirror config scope)" ...``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.config import schema
from sim_mirror.config.writer import ConfigError, ConfigWriter
from sim_mirror.daemon.app import SERVER_SCOPE
from sim_mirror.daemon.lifecycle import read_info
from sim_mirror.mcp.launcher import DaemonUnavailable
from sim_mirror.scope import ID_PATTERN, Scope


def register(commands: Any) -> None:
    command = commands.add_parser("config", help="show or change SimMirror's settings")
    actions = command.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("path", help="print where config.toml is")
    actions.add_parser("validate", help="check config.toml and the SIM_MIRROR_ environment")
    scoping = actions.add_parser("scope", help="print the scope id SimMirror gives a folder")
    scoping.add_argument("--folder", help="this folder instead of the current one")
    for name, helped in (("list", "every setting and its value"), ("get", "one setting's value")):
        action = actions.add_parser(name, help=helped)
        if name == "get":
            action.add_argument("name", help="a setting, such as stream.fps")
        action.add_argument("--scope", help="as this scope sees it")
    changing = actions.add_parser("set", help="set a setting in config.toml")
    changing.add_argument("name")
    changing.add_argument("value")
    changing.add_argument("--scope", help="in this scope's own table")
    removing = actions.add_parser("unset", help="remove a setting from config.toml")
    removing.add_argument("name")
    removing.add_argument("--scope", help="from this scope's own table")
    copying = actions.add_parser("copy", help="copy one scope's own settings into another scope's table")
    copying.add_argument("--from", dest="source", required=True, metavar="SCOPE", help="the scope to copy from")
    copying.add_argument("--scope", required=True, help="the scope to copy into; what it sets already is kept")
    command.set_defaults(handler=run)


def shown(value: object) -> str:
    return json.dumps(list(value) if isinstance(value, tuple) else value)


def _setting(name: str) -> schema.Setting:
    setting = schema.find(name)
    if setting is None:
        raise ConfigError(f"{name} is not a setting; `sim-mirror config list` shows them all")
    return setting


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    path = ctx.config_path()
    if args.action == "path":
        ctx.say(str(path))
        return 0
    if args.action == "scope":
        ctx.say(_folder_scope(ctx, args.folder))
        return 0
    source = ctx.config()
    if args.action == "validate":
        problems = source.problems()
        for problem in problems:
            ctx.complain(problem)
        if not problems:
            ctx.say(f"config ok: {path}")
        return 1 if problems else 0
    scope = Scope.named(args.scope) if args.scope else SERVER_SCOPE
    writer = ConfigWriter(path)
    if args.action == "list":
        config, origins = source.get(scope), source.explain(scope)
        for setting in schema.SETTINGS:
            origin = origins[setting.key]
            said = "" if origin.layer == "file" else f"  # {origin.label()}"
            ctx.say(f"{setting.path} = {shown(getattr(config, setting.key))}{said}")
        return 0
    if args.action == "get":
        ctx.say(shown(getattr(source.get(scope), _setting(args.name).key)))
        return 0
    if args.action == "copy":
        if not _copy(ctx, writer, args.source, args.scope):
            return 0
    elif args.action == "set":
        value = writer.set(args.name, args.value, scope=args.scope)
        ctx.say(f"{_setting(args.name).path} = {shown(value)}")
    else:
        removed = writer.unset(args.name, scope=args.scope)
        ctx.say(f"{_setting(args.name).path} unset" if removed else f"{args.name} was not set in {path}")
    _reload(ctx)
    return 0


def _folder_scope(ctx: CliContext, named: str | None) -> str:
    """The scope id of the folder named, relative to where the command runs, else of the folder it runs in."""
    if named is None:
        return Scope.for_folder(ctx.cwd).id
    folder = ctx.cwd / Path(named).expanduser()
    if not folder.is_dir():
        raise ConfigError(f"{named} is not a folder")
    return Scope.for_folder(folder).id


def _copy(ctx: CliContext, writer: ConfigWriter, source: str, target: str) -> bool:
    """Copy what one scope's table sets into another's, keeping what that one sets already. Answers whether anything
    was written."""
    for scope_id in (source, target):
        if not ID_PATTERN.match(scope_id):
            raise ConfigError(f"{scope_id!r} is not a scope id")
    if source == target:
        raise ConfigError("a scope's settings are copied into another scope, not into its own")
    kept = writer.values(scope=target)
    # Only what a scope may have: a setting only the whole daemon reads does nothing in a scope's table.
    values = {key: value for key, value in writer.values(scope=source).items() if _setting(key).reach == "scope"}
    for key in sorted(values.keys() & kept.keys()):
        ctx.say(f"{_setting(key).path} kept: {target} sets it already")
    copied = {key: value for key, value in values.items() if key not in kept}
    if not copied:
        ctx.say(f"nothing copied: {source} sets nothing of its own that {target} does not")
        return False
    for path, value in writer.change(copied, scope=target).set.items():
        ctx.say(f"{path} = {shown(value)}  # copied from {source}")
    return True


def _reload(ctx: CliContext) -> None:
    """Have a running daemon apply the change now."""
    info = read_info(ctx.state.run_dir())
    if info is None:
        return
    try:
        ctx.client(info.url).post("/api/v1/admin/reload", {})
    except DaemonUnavailable as exc:
        ctx.complain(f"the running daemon did not reload ({exc}); it applies the change when it next starts")
        return
    ctx.say(f"the daemon at {info.url} applied it")
