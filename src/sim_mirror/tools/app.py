# SPDX-License-Identifier: Apache-2.0
"""`sim_app`: launching, quitting and installing apps, opening URLs, reading the device's log, and what is installed.

What a call may reach is checked here: a URL's scheme, a launch's arguments, and an app to install, which must be a
built ``.app`` -- an ``.apk``, or a folder of an app's split APKs, for an Android device -- inside a folder the scope
may install from (`Policy.install_roots`), with every link followed first.

``info`` says which app is installed under a bundle id, and -- for one `sim_build_run` stamped (`build.stamp`) -- the
commit it was built from, and whether that is the code in the folder builds run in now, so an agent can check before
it records or reports what the app does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sim_mirror.build.provider import working_folder
from sim_mirror.build.stamp import SHA_KEY, compared, described, stamp_of
from sim_mirror.connectors.base import Capability
from sim_mirror.core.control import AppControl, LogReader
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.protocol import DeviceKind
from sim_mirror.tools.context import ToolContext, flag_arg, make_tool, ready_device, require, whole_arg
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import LAUNCH_ARGS_MAX, LOG_LINES, LOG_SINCE_S

URL_MAX = 2000
LAUNCH_ARG_MAX = 500
FILTER_MAX = 200
#: Each action, and what the device's connector must be able to do for it.
NEEDS: dict[str, Capability] = {
    "launch": Capability.APP_LAUNCH,
    "terminate": Capability.APP_LAUNCH,
    "install": Capability.APP_INSTALL,
    "open_url": Capability.OPEN_URL,
    "logs": Capability.LOGS,
    "info": Capability.APP_LAUNCH,
}
ACTIONS = tuple(NEEDS)
#: Schemes an agent may not open on the device: local files, inline documents, script, and the device's settings --
#: an Android intent, which names any activity, and a content provider's data among them.
REFUSED_SCHEMES = frozenset(
    {"file", "data", "javascript", "about", "x-apple.systempreferences", "prefs", "app-prefs", "intent", "content"}
)

_SCHEME = re.compile(r"\A([A-Za-z][A-Za-z0-9+.-]*):")
#: An app's id: an iOS bundle identifier, or an Android package name, which may hold an underscore.
_BUNDLE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def _bundle(value: object) -> str:
    if not isinstance(value, str) or not _BUNDLE_ID.match(value):
        raise ToolRefused(
            "bundle_id must be an app's id: a bundle identifier like com.example.Notes, or an Android package name "
            "like com.example.notes"
        )
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
        raise ToolRefused(f"{match.group(1)}: URLs are not opened on the device")
    return match.string


def _built(path: Path, kind: DeviceKind) -> bool:
    """Whether a path is an app built for this kind of device: an ``.app`` bundle, or an ``.apk`` or a folder of an
    Android app's split APKs."""
    if kind == "android":
        return (path.suffix == ".apk" and path.is_file()) or (path.is_dir() and any(path.glob("*.apk")))
    return path.suffix == ".app" and (path / "Info.plist").is_file()


def installable(value: object, roots: tuple[Path, ...], kind: DeviceKind = "simulator") -> Path:
    """A built app inside a folder this scope may install from, with every link followed first."""
    what = "an .apk, or a folder of an app's split APKs" if kind == "android" else "a built .app"
    if not isinstance(value, str) or not value.startswith("/"):
        raise ToolRefused(f"path must be the absolute path of {what}")
    real = Path(value).resolve()
    if not _built(real, kind):
        raise ToolRefused(f"{value} is not {what}")
    if not any(real.is_relative_to(root.resolve()) for root in roots):
        places = ", ".join(str(root) for root in roots) or "no folder here"
        raise ToolRefused(f"only an app built inside {places} can be installed")
    return real


async def _logs(args: dict[str, Any], instance: DeviceInstance, control: LogReader) -> str:
    since = whole_arg(args.get("since_s"), 60, LOG_SINCE_S, "since_s")
    lines = whole_arg(args.get("lines"), 50, LOG_LINES, "lines")
    wanted = args.get("filter")
    if wanted is not None and (not isinstance(wanted, str) or len(wanted) > FILTER_MAX):
        raise ToolRefused(f"filter is text of at most {FILTER_MAX} characters")
    bundle = _bundle(args["bundle_id"]) if args.get("bundle_id") is not None else None
    shown = await control.logs(instance.udid, since_s=since, bundle_id=bundle)
    rows = [row for row in shown if not wanted or wanted.casefold() in row.casefold()]
    return "\n".join(rows[-lines:]) if rows else f"no log lines in the last {since}s"


async def _info(args: dict[str, Any], instance: DeviceInstance, control: AppControl, ctx: ToolContext) -> str:
    bundle = _bundle(args.get("bundle_id"))
    app = await control.app_info(instance.udid, bundle)
    if app is None:
        return f"{bundle} is not installed on {instance.name}"
    lines = [" · ".join(part for part in (bundle, f"{app.name} {app.version}".strip(), app.location) if part)]
    mark = stamp_of(app.info) if app.info is not None else None
    if app.info is None and instance.kind == "android":
        lines.append("no stamp: an app on an Android phone is never stamped")
    elif app.info is None:
        lines.append("no stamp: a real device's app is never stamped, and its Info.plist is not read off the device")
    elif mark is None:
        lines.append("no stamp: it was installed with build.stamp_source off, or not by sim_build_run")
    else:
        lines.append(f"stamped {described(mark)} ({SHA_KEY})")
        # Compared only where this scope builds: reading the folder's checkout is part of building it.
        if ctx.builds is not None and ctx.folder is not None and ctx.config.build_tools and ctx.shells_allowed:
            folder, notes = working_folder(args, ctx, ctx.folder)
            lines += [*notes, compared(mark, await ctx.builds.source_of(folder), folder)]
    return "\n".join(lines)


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    action = args.get("action")
    if action not in ACTIONS:
        raise ToolRefused(f"action is one of {', '.join(ACTIONS)}")
    instance = await ready_device(ctx)
    require(ctx, instance, (NEEDS[action],), f"sim_app {action}")
    control = ctx.manager.control(instance)
    if action == "launch":
        bundle, launch_args = _bundle(args.get("bundle_id")), _launch_args(args.get("args"))
        relaunch = flag_arg(args.get("relaunch"), "relaunch")
        verb = "relaunch" if relaunch else "launch"
        launching = control.launch(instance.udid, bundle, launch_args, terminate_running=relaunch)
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
        ending = control.terminate(instance.udid, bundle)
        ended = await ctx.actions.announced(instance, ctx.caller, "app", f"quit {bundle}", ending)
        instance.launched.pop(bundle, None)
        return text(f"terminated {bundle}" if ended else f"{bundle} was not running")
    if action == "install":
        app = installable(args.get("path"), ctx.roots, instance.kind)
        installing = control.install(instance.udid, str(app))
        await ctx.actions.announced(instance, ctx.caller, "app", f"install {app.name}", installing)
        return text(f"installed {app.name}")
    if action == "open_url":
        url = _url(args.get("url"))
        await ctx.actions.announced(instance, ctx.caller, "app", f"open {url}", control.openurl(instance.udid, url))
        return text(f"opened {url}")
    if action == "info":
        return text(await _info(args, instance, control, ctx))
    return text(await _logs(args, instance, control))


TOOL = make_tool("sim_app", (Capability.APP_LAUNCH,), run)
