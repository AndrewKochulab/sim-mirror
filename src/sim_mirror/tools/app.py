# SPDX-License-Identifier: Apache-2.0
"""`sim_app`: launching, quitting and installing apps, opening URLs, and reading the device's log.

What a call may reach is checked here: a URL's scheme, a launch's arguments, and an app to install, which must be a
built ``.app`` inside a folder the scope may install from (`Policy.install_roots`), with every link followed first.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.simctl import Simctl
from sim_mirror.tools.context import ToolContext, make_tool, ready_device, whole_arg
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import LAUNCH_ARGS_MAX, LOG_LINES, LOG_SINCE_S

URL_MAX = 2000
LAUNCH_ARG_MAX = 500
FILTER_MAX = 200
ACTIONS = ("launch", "terminate", "install", "open_url", "logs")
#: Schemes an agent may not open on the device: local files, inline documents, script, and the device's settings.
REFUSED_SCHEMES = frozenset({"file", "data", "javascript", "about", "x-apple.systempreferences", "prefs"})

_SCHEME = re.compile(r"\A([A-Za-z][A-Za-z0-9+.-]*):")
_BUNDLE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.-]{0,254}\Z")


def _bundle(value: object) -> str:
    if not isinstance(value, str) or not _BUNDLE_ID.match(value):
        raise ToolRefused("bundle_id must be a bundle identifier, like com.example.Notes")
    return value


def _launch_args(value: object) -> list[str]:
    if value is None:
        return []
    if not (
        isinstance(value, list)
        and len(value) <= LAUNCH_ARGS_MAX
        and all(isinstance(arg, str) and len(arg) <= LAUNCH_ARG_MAX and "\x00" not in arg for arg in value)
    ):
        raise ToolRefused(f"args must be a list of at most {LAUNCH_ARGS_MAX} strings")
    return value


def _url(value: object) -> str:
    match = _SCHEME.match(value) if isinstance(value, str) and len(value) <= URL_MAX else None
    if match is None:
        raise ToolRefused(f"url must be a URL with a scheme, at most {URL_MAX} characters")
    if match.group(1).lower() in REFUSED_SCHEMES:
        raise ToolRefused(f"{match.group(1)}: URLs are not opened on the simulator")
    return match.string


def installable(value: object, roots: tuple[Path, ...]) -> Path:
    """A built app inside a folder this scope may install from, with every link followed first."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise ToolRefused("path must be the absolute path of a built .app")
    real = Path(value).resolve()
    if real.suffix != ".app" or not (real / "Info.plist").is_file():
        raise ToolRefused(f"{value} is not a built .app")
    if not any(real.is_relative_to(root.resolve()) for root in roots):
        places = ", ".join(str(root) for root in roots) or "no folder here"
        raise ToolRefused(f"only an app built inside {places} can be installed")
    return real


async def _logs(args: dict[str, Any], instance: DeviceInstance, simctl: Simctl) -> str:
    since = whole_arg(args.get("since_s"), 60, LOG_SINCE_S, "since_s")
    lines = whole_arg(args.get("lines"), 50, LOG_LINES, "lines")
    wanted = args.get("filter")
    if wanted is not None and (not isinstance(wanted, str) or len(wanted) > FILTER_MAX):
        raise ToolRefused(f"filter is text of at most {FILTER_MAX} characters")
    if args.get("bundle_id") is not None:
        bundle = _bundle(args["bundle_id"])
        predicate = f'subsystem == "{bundle}" OR process == "{bundle.rsplit(".", 1)[-1]}"'
    else:
        predicate = "messageType == error OR messageType == fault"
    shown = await simctl.log_show(instance.udid, since_s=since, predicate=predicate)
    rows = [
        row for row in shown.splitlines()[1:] if row.strip() and (not wanted or wanted.casefold() in row.casefold())
    ]
    return "\n".join(rows[-lines:]) if rows else f"no log lines in the last {since}s"


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action")
    if action not in ACTIONS:
        raise ToolRefused(f"action is one of {', '.join(ACTIONS)}")
    instance = await ready_device(ctx)
    simctl = ctx.manager.simctl(instance)
    if action == "launch":
        bundle, launch_args = _bundle(args.get("bundle_id")), _launch_args(args.get("args"))
        relaunch = args.get("relaunch", False)
        if not isinstance(relaunch, bool):
            raise ToolRefused("relaunch is true or false")
        verb = "relaunch" if relaunch else "launch"
        launching = simctl.launch(instance.udid, bundle, launch_args, terminate_running=relaunch)
        pid = await ctx.actions.announced(instance, ctx.caller, "app", f"{verb} {bundle}", launching)
        if pid is not None and not relaunch and instance.launched.get(bundle) == pid:
            return text(
                f"{bundle} was already running (pid {pid}) and is in front again; relaunch: true starts it fresh"
            )
        if pid is not None:
            instance.launched[bundle] = pid
        return text(f"{verb}ed {bundle}" + (f" (pid {pid})" if pid else ""))
    if action == "terminate":
        bundle = _bundle(args.get("bundle_id"))
        ending = simctl.terminate(instance.udid, bundle)
        ended = await ctx.actions.announced(instance, ctx.caller, "app", f"quit {bundle}", ending)
        instance.launched.pop(bundle, None)
        return text(f"terminated {bundle}" if ended else f"{bundle} was not running")
    if action == "install":
        app = installable(args.get("path"), ctx.roots)
        installing = simctl.install(instance.udid, str(app))
        await ctx.actions.announced(instance, ctx.caller, "app", f"install {app.name}", installing)
        return text(f"installed {app.name}")
    if action == "open_url":
        url = _url(args.get("url"))
        await ctx.actions.announced(instance, ctx.caller, "app", f"open {url}", simctl.openurl(instance.udid, url))
        return text(f"opened {url}")
    return text(await _logs(args, instance, simctl))


TOOL = make_tool("sim_app", (Capability.APP_LAUNCH,), run)
