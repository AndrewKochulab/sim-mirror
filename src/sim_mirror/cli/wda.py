# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror wda``: WebDriverAgent, which lets SimMirror touch, type on and read a cabled real device.

* ``teams`` lists the development teams this Mac's certificates sign for;
* ``setup`` sets it up as the viewer's Set up touch does (`connectors.iphone.wda_setup`): fetches the pinned release,
  checks it, keeps its screen stream on the device, and builds it with the team this folder's project signs with
  (`core.signing`) and the scope's Xcode -- then has a running daemon start it on the device, and says what to do on
  the device the first time;
* ``status`` says what is set up, and what is not;
* ``uninstall`` removes WebDriverAgent's runner from a device.

Nothing here installs anything on a device itself: the runner is installed when a session first starts it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sim_mirror.build.wda import runner_bundle_id, xctestrun
from sim_mirror.cli.context import CliContext
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.iphone.wda_setup import WdaSetup, derived_for, wda_root
from sim_mirror.connectors.iphone.wda_source import PINNED
from sim_mirror.core.signing import SigningTeam, SigningTeams
from sim_mirror.host_copy import HostCopy
from sim_mirror.mcp.launcher import DaemonUnavailable
from sim_mirror.platform.devicectl import Devicectl, DevicectlError
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.platform.keychain import development_teams
from sim_mirror.scope import Scope

#: What the device asks the first time WebDriverAgent runs on it.
ON_DEVICE = (
    "On the device, the first time: trust your developer in Settings > General > VPN & Device Management if it asks, "
    "and turn on Settings > Developer > Enable UI Automation."
)
#: Where a team came from, as a person reads it.
FROM = {"project": "this folder's project's", "setting": "real_devices.team_id", "mac": "the only team here"}


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


async def _signed(args: argparse.Namespace, ctx: CliContext) -> tuple[Scope, SimConfig, SigningTeam | None]:
    """The scope, its settings, and the team that signs WebDriverAgent for it: this folder's project's first, unless a
    scope is named."""
    scope = ctx.scope(args.scope)
    config = ctx.config().get(scope)
    folder = None if args.scope else ctx.cwd
    teams = SigningTeams(lambda _: folder, lambda: development_teams(ctx.run))
    return scope, config, await teams.of(scope, config)


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
    if args.device is not None and not is_device_udid(args.device):
        ctx.complain(f"sim-mirror: {args.device} is not a real device's UDID; `sim-mirror devices list` shows them")
        return 1
    scope, config, found = await _signed(args, ctx)
    if found is None:
        ctx.complain(f"sim-mirror: {copy.wda_needs_team()}")
        return 1
    team = found.team
    ctx.say(f"signing with team {team} ({FROM[found.source]})")
    now = datetime.now(timezone.utc)
    if not any(known.team_id == team and known.valid(now) for known in await development_teams(ctx.run)):
        ctx.say(f"no certificate on this Mac signs for {team} yet: Xcode makes one if its account is signed in")
    setup = WdaSetup(root=lambda: wda_root(ctx.env), fetch=ctx.fetch, xcrun=ctx.xcrun)
    done = await setup.wait(setup.start(team, _xcode(config), args.device, config.wda_path, say=ctx.say))
    if done.state == "failed":
        ctx.complain(f"sim-mirror: {copy.wda_setup_failed(done.said)}")
        return 1
    ctx.say(f"built WebDriverAgent as {runner_bundle_id(team)}")
    if not config.wda_enabled:
        ctx.say("it is off here: turn it on with `sim-mirror config set real_devices.wda.enabled true`")
    elif args.device is not None:
        _start_on_device(ctx, scope)
    ctx.say(ON_DEVICE)
    return 0


def _start_on_device(ctx: CliContext, scope: Scope) -> None:
    """Have a running daemon start WebDriverAgent on the scope's device, when it is the one just built for."""
    client = ctx.client()
    if not client.healthy():
        ctx.say("pick the device in the viewer, or with `sim-mirror devices choose`, to use it")
        return
    try:
        said = client.admin("POST", f"/api/v1/scopes/{scope.id}/device/touch")
    except DaemonUnavailable as exc:
        ctx.say(f"the daemon did not start it ({exc}); pick the device again to use it")
        return
    if said.get("state") in ("starting", "ready"):
        ctx.say("the daemon is starting it on the device")
    else:
        ctx.say("pick the device in the viewer, or with `sim-mirror devices choose`, to use it")


async def _status(args: argparse.Namespace, ctx: CliContext) -> int:
    _, config, found = await _signed(args, ctx)
    team, root = (found.team if found else ""), wda_root(ctx.env)
    source = config.wda_path or str(root / "source" / PINNED.folder)
    built = xctestrun(derived_for(root, team, _xcode(config))) if team else None
    status = {
        "enabled": config.wda_enabled,
        "team_id": team or None,
        "team_from": found.source if found else None,
        "source": source,
        "built": str(built) if built else None,
        "bundle_id": runner_bundle_id(team) if team else None,
    }
    if args.json:
        ctx.say(json.dumps(status, indent=2))
    else:
        ctx.say(f"WebDriverAgent: {'on' if config.wda_enabled else 'off'} (real_devices.wda.enabled)")
        ctx.say(f"team: {f'{team} ({FROM[found.source]})' if found else 'none known -- set real_devices.team_id'}")
        ctx.say(f"source: {source}")
        ctx.say(f"built: {built or 'no -- Set up touch in the viewer, or `sim-mirror wda setup`, builds it'}")
    return 0 if config.wda_enabled and built else 1


async def _uninstall(args: argparse.Namespace, ctx: CliContext) -> int:
    _, config, found = await _signed(args, ctx)
    if not is_device_udid(args.udid) or found is None:
        ctx.complain("sim-mirror: name a real device's UDID, with the team that built WebDriverAgent known here")
        return 1
    runner = f"{runner_bundle_id(found.team)}.xctrunner"
    try:
        await Devicectl(ctx.xcrun, developer_dir=_xcode(config)).uninstall(args.udid, runner)
    except DevicectlError as exc:
        ctx.complain(f"sim-mirror: {runner} could not be removed: {exc}")
        return 1
    ctx.say(f"removed {runner} from {args.udid}")
    return 0
