# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror wda``: WebDriverAgent, which lets SimMirror touch, type on and read a cabled real device.

* ``teams`` lists the development teams this Mac's certificates sign for, to set ``real_devices.team_id`` to one;
* ``setup`` fetches the pinned release (`connectors.iphone.wda_source`), checks it, keeps its screen stream on the
  device, and builds it with that team and the scope's Xcode -- and says what to do on the device the first time;
* ``status`` says what is set up, and what is not;
* ``uninstall`` removes WebDriverAgent's runner from a device.

Nothing here installs anything on a device: the runner is installed when a session first starts it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sim_mirror.build.wda import build_wda, runner_bundle_id, xctestrun
from sim_mirror.cli.context import CliContext
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.iphone.wda import derived_for, wda_root
from sim_mirror.connectors.iphone.wda_source import PINNED, WdaSourceError, configured, wda_source
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.platform.keychain import development_teams

#: What the device asks the first time WebDriverAgent runs on it.
ON_DEVICE = (
    "On the device, the first time: trust your developer in Settings > General > VPN & Device Management, and turn "
    "on Settings > Developer > Enable UI Automation."
)


def register(commands: Any) -> None:
    command = commands.add_parser("wda", help="set up WebDriverAgent to touch, type on and read a real device")
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    status = actions.add_parser("status", help="say what is set up for WebDriverAgent")
    status.add_argument("--json", action="store_true", help="print it as JSON")
    status.add_argument("--scope", help="for this scope's settings, instead of this folder's project's")
    actions.add_parser("teams", help="list the development teams this Mac can sign WebDriverAgent with")
    setup = actions.add_parser("setup", help="fetch WebDriverAgent and build it with your team")
    setup.add_argument("--scope", help="with this scope's settings, instead of this folder's project's")
    setup.add_argument(
        "--device", help="the UDID of a cabled device to build it for, which Xcode registers with your team"
    )
    uninstall = actions.add_parser("uninstall", help="remove WebDriverAgent from a device")
    uninstall.add_argument("udid", help="the device's UDID, as `sim-mirror devices list` shows it")
    uninstall.add_argument("--scope", help="with this scope's settings, instead of this folder's project's")
    command.set_defaults(handler=run, action="status", json=False, scope=None, device=None)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    actions = {"teams": _teams, "setup": _setup, "uninstall": _uninstall}
    return asyncio.run(actions.get(args.action, _status)(args, ctx))


def _xcode(config: SimConfig) -> str:
    return config.real_devices_developer_dir or config.developer_dir


async def _teams(args: argparse.Namespace, ctx: CliContext) -> int:
    teams = await development_teams(ctx.run)
    if not teams:
        ctx.complain(
            "sim-mirror: no Apple Development certificate is on this Mac: sign in to Xcode > Settings > Accounts"
        )
        return 1
    now = datetime.now(timezone.utc)
    for team in teams:
        state = f"until {team.expires:%Y-%m-%d}" if team.valid(now) else "expired"
        ctx.say(f"{team.team_id}  {team.name}  ({state})")
    return 0


async def _setup(args: argparse.Namespace, ctx: CliContext) -> int:
    copy = HostCopy()
    config = ctx.config().get(ctx.scope(args.scope))
    team, xcode = config.real_devices_team_id, _xcode(config)
    if not team:
        ctx.complain(f"sim-mirror: {copy.wda_needs_team()} `sim-mirror wda teams` lists the teams here.")
        return 1
    if args.device is not None and not is_device_udid(args.device):
        ctx.complain(f"sim-mirror: {args.device} is not a real device's UDID; `sim-mirror devices list` shows them")
        return 1
    now = datetime.now(timezone.utc)
    if not any(known.team_id == team and known.valid(now) for known in await development_teams(ctx.run)):
        ctx.say(f"no certificate on this Mac signs for {team} yet: Xcode makes one if its account is signed in")
    root = wda_root(ctx.env)
    try:
        if config.wda_path:
            source = configured(config.wda_path)
        else:
            if not (root / "source" / PINNED.folder).is_dir():
                ctx.say(f"fetching WebDriverAgent {PINNED.version} ({PINNED.commit[:12]}) and checking its SHA-256")
            source = await asyncio.to_thread(wda_source, root / "source", PINNED, fetch=ctx.fetch)
    except WdaSourceError as exc:
        ctx.complain(f"sim-mirror: {exc}")
        return 1
    target = args.device or "any iPhone the team's profile covers"
    ctx.say(f"building WebDriverAgent from {source} for {target} with team {team}; a first build takes a minute or two")
    built = await build_wda(source, derived_for(root, team, xcode), team, xcode, udid=args.device, xcrun=ctx.xcrun)
    if built.xctestrun is None:
        ctx.complain(
            f"sim-mirror: WebDriverAgent did not build: {built.failure}. If Xcode's account was refused, sign in "
            "again in Xcode > Settings > Accounts."
        )
        return 1
    ctx.say(f"built WebDriverAgent as {runner_bundle_id(team)}")
    ctx.say("turn it on with `sim-mirror config set real_devices.wda.enabled true`, then pick the device again")
    ctx.say(ON_DEVICE)
    return 0


async def _status(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    team, root = config.real_devices_team_id, wda_root(ctx.env)
    source = config.wda_path or str(root / "source" / PINNED.folder)
    built = xctestrun(derived_for(root, team, _xcode(config))) if team else None
    status = {
        "enabled": config.wda_enabled,
        "team_id": team or None,
        "source": source,
        "built": str(built) if built else None,
        "bundle_id": runner_bundle_id(team) if team else None,
    }
    if args.json:
        ctx.say(json.dumps(status, indent=2))
    else:
        ctx.say(f"WebDriverAgent: {'on' if config.wda_enabled else 'off'} (real_devices.wda.enabled)")
        ctx.say(f"team: {team or 'not set (real_devices.team_id)'}")
        ctx.say(f"source: {source}")
        ctx.say(f"built: {built or 'no -- `sim-mirror wda setup` builds it'}")
    return 0 if config.wda_enabled and built else 1


async def _uninstall(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    team = config.real_devices_team_id
    if not is_device_udid(args.udid) or not team:
        ctx.complain("sim-mirror: name a real device's UDID, with real_devices.team_id set to the team that built it")
        return 1
    runner = f"{runner_bundle_id(team)}.xctrunner"
    try:
        await Devicectl(ctx.xcrun, developer_dir=_xcode(config)).uninstall(args.udid, runner)
    except DevicectlError as exc:
        ctx.complain(f"sim-mirror: {runner} could not be removed: {exc}")
        return 1
    ctx.say(f"removed {runner} from {args.udid}")
    return 0
