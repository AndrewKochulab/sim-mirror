# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror android``: Android emulators and phones, which SimMirror shows and drives through adb and scrcpy.

* ``status`` says what is set up -- the SDK, the devices adb lists, scrcpy's server, the JDK Gradle builds with -- as
  the doctor's Android checks do (`doctor.android`);
* ``avds`` lists the virtual devices made with the SDK, and what stands in the way of one that cannot start;
* ``setup`` fetches the scrcpy server SimMirror pins and checks its hash, as the viewer's Set up touch does -- then has
  a running daemon start it on the scope's Android device;
* ``clean`` removes what SimMirror left on the devices adb lists: this host's scrcpy server, and the stamps an
  emulator keeps of the apps SimMirror built.

Nothing here starts an emulator, or adb's server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.android.scrcpy import remote_jar
from sim_mirror.connectors.android.scrcpy_source import ScrcpySourceError
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.core.android_control import STAMPS
from sim_mirror.doctor import android
from sim_mirror.doctor.checks import DoctorContext
from sim_mirror.doctor.report import Report
from sim_mirror.mcp.launcher import DaemonUnavailable
from sim_mirror.platform.adb import AdbError

SCOPE_HELP = "with this scope's settings, instead of this folder's project's"
#: The checks ``status`` runs, in the doctor's order.
CHECKS = (android.check_android_sdk, android.check_android_devices, android.check_scrcpy, android.check_gradle)


def register(commands: Any) -> None:
    command = commands.add_parser("android", help="set up and inspect Android emulators and phones")
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    status = actions.add_parser("status", help="say what is set up for Android devices (the default)")
    status.add_argument("--json", action="store_true", help="print it as JSON")
    status.add_argument("--scope", help=SCOPE_HELP)
    actions.add_parser("avds", help="list the Android virtual devices made with the SDK")
    setup = actions.add_parser("setup", help="fetch scrcpy's server, to see an Android device live and touch it fast")
    setup.add_argument("--scope", help=SCOPE_HELP)
    actions.add_parser("clean", help="remove what SimMirror left on the Android devices adb lists")
    command.set_defaults(handler=run, action="status", json=False, scope=None)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    actions = {"avds": _avds, "setup": _setup, "clean": _clean}
    return asyncio.run(actions.get(args.action, _status)(args, ctx))


async def _status(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    doctor = DoctorContext(
        config=config, registry=ConnectorRegistry([]), run=ctx.run, env=ctx.env, **ctx.doctor_android()
    )
    report = Report(tuple([await check(doctor) for check in CHECKS]))
    ctx.say(json.dumps(report.to_dict(), indent=2) if args.json else report.text())
    return report.exit_code


async def _avds(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(None))
    sdk = ctx.android_sdk(config.android_sdk_path)
    found = ctx.avds(sdk)
    if not found:
        where = f" made with {sdk.root}" if sdk else ""
        ctx.say(f"no Android virtual devices{where}: make one in Android Studio's Device Manager")
        return 0
    for avd in found:
        api = f"API {avd.api}" if avd.api is not None else "API unknown"
        problem = f"  ({avd.problem})" if avd.problem else ""
        ctx.say(f"{avd.name}  {avd.display_name}  {api}{problem}")
    return 0


async def _setup(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    config = ctx.config().get(scope)
    jars = ctx.scrcpy_jars()
    jar = jars.find()
    if jar is None:
        ctx.say(f"fetching scrcpy's server, release {jars.release.version}, from {jars.release.url}")
        try:
            jar = await asyncio.to_thread(jars.fetch_now)
        except ScrcpySourceError as exc:
            ctx.complain(f"sim-mirror: {exc}")
            return 1
    ctx.say(f"scrcpy's server {jar.release.version} is set up at {jar.path}, checked by its SHA-256")
    if config.scrcpy_path:
        ctx.say(f"this scope uses {config.scrcpy_path} instead (android.scrcpy.path)")
    if not config.scrcpy:
        ctx.say("it is off here: turn it on with `sim-mirror config set android.scrcpy.enabled true`")
        return 0
    client = ctx.client()
    if not client.healthy():
        ctx.say("choose an Android device in the viewer, or with `sim-mirror devices choose`, to use it")
        return 0
    try:
        said = client.admin("POST", f"/api/v1/scopes/{scope.id}/device/touch")
    except DaemonUnavailable as exc:
        ctx.say(f"the daemon did not start it ({exc}); choose the device again to use it")
        return 0
    if said.get("state") in ("starting", "ready"):
        ctx.say("the daemon is starting it on the device")
    else:
        ctx.say("choose an Android device in the viewer, or with `sim-mirror devices choose`, to use it")
    return 0


async def _clean(args: argparse.Namespace, ctx: CliContext) -> int:
    adb = ctx.adb()
    try:
        devices = [device for device in await adb.devices() if device.state == "device"]
    except AdbError:
        ctx.say("adb's server is not running, so no device has anything of SimMirror's to remove")
        return 0
    jar = remote_jar(ctx.state.owner_tag)
    failed = 0
    for device in devices:
        try:
            await adb.run(device.serial, ["rm", "-f", jar])
            await adb.run(device.serial, ["rm", "-rf", STAMPS])
        except AdbError as exc:
            ctx.complain(f"sim-mirror: {device.serial}: {exc}")
            failed += 1
            continue
        ctx.say(f"{device.serial}: removed {jar} and {STAMPS}")
    if not devices:
        ctx.say("no Android device is running or connected")
    return 1 if failed else 0
