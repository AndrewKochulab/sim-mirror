# SPDX-License-Identifier: Apache-2.0
"""The build tools -- `sim_build_run` and `sim_test` -- listed while a scope's ``build.tools`` is on.

A build runs xcodebuild, which is running commands: the tools are refused on every call unless the host also lets the
scope run commands (`Policy.shells_allowed`). A build that succeeded is installed and launched on the scope's device,
drawn for anyone watching; while tests run the device takes no agent gestures, and every screen watching is told why.
A build of another app than the scope's ``build.expected_bundle_id`` is not installed: a configuration with a bundle
id of its own would put a second app beside the one a person uses.

A test run may be sent to another simulator on this Mac instead (`destination`). Not to one that is somebody else's:
another scope's running device, one another scope's tests are running on, or one another process on the Mac has
claimed. xcodebuild boots it if it has to, and it is left running, as Xcode leaves it.

A run builds the folder the agent works in (`build.checkouts`): the one it names in ``folder``, else the first folder
its client works in (its MCP roots) that this scope may build -- the build folder, or the same folder of another
checkout of its repository, such as a git worktree -- else the build folder, and a client working somewhere else is
told so. A build in another folder than the build folder runs with that folder's own settings, when a person gave it
some.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sim_mirror.build.checkouts import FOLDER_MAX, build_folder_for
from sim_mirror.build.destination import choose_destination, described
from sim_mirror.build.stamp import discard
from sim_mirror.build.xcodebuild import Build, BuildRefused, BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import ActionError
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager, SimulatorUnavailable
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.tools.context import Tool, ToolContext, flag_arg, make_tool, ready_device, whole_arg
from sim_mirror.tools.results import Result, ToolRefused, text
from sim_mirror.tools.schemas import (
    BUILD_INSTRUCTIONS,
    BUILD_WAIT_BOUNDS,
    BUILD_WAIT_S,
    SHELL_INSTRUCTIONS,
    TEST_RETRIES_BOUNDS,
)

NEEDS = (Capability.LIFECYCLE, Capability.APP_INSTALL, Capability.APP_LAUNCH)


def _builds(ctx: ToolContext) -> tuple[BuildRunner, Path]:
    """The build runner and the build folder -- or why this scope builds nothing now."""
    if not ctx.config.build_tools:
        raise ToolRefused(ctx.copy.build_tools_off())
    if not ctx.shells_allowed:
        raise ToolRefused(ctx.copy.shells_not_allowed())
    if ctx.builds is None:
        raise ToolRefused("The build runner is not running.")
    if ctx.folder is None:
        raise ToolRefused(ctx.copy.no_build_folder())
    return ctx.builds, ctx.folder


def _named_folder(value: object, home: Path) -> Path:
    """The folder a call's ``folder`` names, where this scope may build -- or why not."""
    if not isinstance(value, str) or not value.strip() or len(value) > FOLDER_MAX or "\x00" in value:
        raise ToolRefused(f"folder is the path of the folder to build in, at most {FOLDER_MAX} characters")
    path = Path(value.strip()).expanduser()
    chosen = build_folder_for(path if path.is_absolute() else home / path, home, exact=True)
    if chosen is None:
        raise ToolRefused(
            f"{value} is not a folder builds may run in here: they run in {home}, a folder inside it, or the same "
            "folder of another checkout of its repository, such as a git worktree"
        )
    if not chosen.is_dir():
        raise ToolRefused(f"{chosen} is not a folder")
    return chosen


def working_folder(args: dict[str, Any], ctx: ToolContext, home: Path) -> tuple[Path, list[str]]:
    """The folder a run builds in -- `home` itself when it is that -- and a note when the agent's client works where
    this scope may not build."""
    if args.get("folder") is not None:
        chosen = _named_folder(args["folder"], home)
        return home if chosen == home.resolve() else chosen, []
    for root in ctx.caller.roots:
        found = build_folder_for(root, home, exact=False)
        if found is not None:
            return home if found == home.resolve() else found, []
    if not ctx.caller.roots:
        return home, []
    return home, [
        f"note: built {home}, not {ctx.caller.roots[0]} where your client works, which builds here may not reach: "
        f"name the folder with folder, or {ctx.copy.build_folder_hint}"
    ]


async def _install_and_launch(build: Build, instance: DeviceInstance, ctx: ToolContext, config: SimConfig) -> list[str]:
    """Put a build that succeeded on the device and open it, drawn for anyone watching. `config` is the settings of
    the folder built, which a worktree may have of its own."""
    if build.app is None or build.bundle_id is None:
        others = [name for name in build.schemes if name != build.scheme]
        there = f"; the other schemes are {', '.join(others)}" if others else ""
        raise BuildRefused(
            f"{build.scheme} builds no app to install -- a framework or a test bundle, say -- so there is nothing to "
            f"launch; name a scheme that builds an iOS app{there}"
        )
    expected = config.build_expected_bundle_id
    if expected and build.bundle_id != expected:
        raise BuildRefused(
            f"{build.scheme} ({build.configuration}) builds {build.bundle_id}, not {expected}, the app "
            "build.expected_bundle_id names, so it was not installed: name the configuration that builds "
            f"{expected} with configuration, or change build.expected_bundle_id"
        )
    app, stamped = build.app, []
    if config.build_stamp_source and ctx.builds is not None:
        app, said = await ctx.builds.stamped(build)
        stamped.append(said)
    control = ctx.manager.control(instance)
    try:
        installing = control.install(instance.udid, str(app))
        await ctx.actions.announced(instance, ctx.caller, "app", f"install {build.app.name}", installing)
        launching = control.launch(instance.udid, build.bundle_id)
        pid = await ctx.actions.announced(instance, ctx.caller, "app", f"launch {build.bundle_id}", launching)
    except (DeviceControlError, ActionError) as exc:
        raise BuildRefused(str(exc)) from exc
    finally:
        # The device keeps its own copy of what it installs.
        if app != build.app:
            discard(app)
    if pid is not None:
        instance.launched[build.bundle_id] = pid
    launched = f"launched {build.bundle_id}" + (f" (pid {pid})" if pid else "")
    return [f"installed {build.app.name}", *stamped, launched]


def _keep_busy(manager: DeviceManager, instance: DeviceInstance, build: Build) -> None:
    """Tests drive the device themselves: an agent's gestures would land in the middle of them.

    Every screen watching is told what the device is busy with, so a person can see why it is not theirs to touch.
    """
    busy = f"running tests ({build.id})"
    instance.busy = busy
    manager.publish_status(instance)

    def free(_: object) -> None:
        if instance.busy == busy:
            instance.busy = None
            manager.publish_status(instance)

    assert build.task is not None
    build.task.add_done_callback(free)


async def _elsewhere(ctx: ToolContext, runner: BuildRunner, value: object) -> tuple[str, str] | None:
    """The simulator a test run's ``destination`` names, and how its answer names it -- None for the scope's own
    device -- or why it is not this scope's to use."""
    try:
        choices = await ctx.manager.devices(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    choice = choose_destination(choices, value)
    udid, named = choice["udid"], described(choice)
    own = ctx.manager.instance(ctx.scope)
    if own is not None and own.udid == udid:
        return None
    noun = ctx.copy.scope_noun
    if any(instance.udid == udid for instance in ctx.manager.instances()):
        raise BuildRefused(f"{named} is another {noun}'s simulator here; name one nobody is using")
    if any(build.udid == udid and build.scope.id != ctx.scope.id for build in runner.runs()):
        raise BuildRefused(f"another {noun}'s tests are running on {named}; name another simulator")
    claim = await ctx.manager.holder(udid)
    if claim is not None:
        raise BuildRefused(ctx.copy.claimed(claim.owner, claim.pid))
    return udid, named


async def _team(ctx: ToolContext, config: SimConfig) -> str:
    """The team that signs a build for a real device when its project names none (`core.signing`)."""
    if ctx.signing is None:
        return config.real_devices_team_id
    found = await ctx.signing.of(ctx.scope, config)
    return found.team if found else ""


async def _run(args: dict[str, Any], ctx: ToolContext, kind: str) -> Result:
    runner, home = _builds(ctx)
    wait_s = whole_arg(args.get("wait_s"), BUILD_WAIT_S, BUILD_WAIT_BOUNDS, "wait_s")
    if args.get("build_id") is not None:
        return text(await runner.result(ctx.scope.id, args["build_id"], wait_s))
    folder, notes = working_folder(args, ctx, home)
    config = ctx.settings_for(folder) if ctx.settings_for is not None and folder != home else ctx.config
    if kind == "build" and args.get("destination") is not None:
        raise ToolRefused("destination is for sim_test: a build is installed and launched on your own simulator")
    elsewhere = await _elsewhere(ctx, runner, args["destination"]) if args.get("destination") is not None else None
    instance: DeviceInstance | None = None
    if elsewhere is not None:
        udid, device = elsewhere
    else:
        instance = await ready_device(ctx)
        udid, device = instance.udid, ""

    async def after(build: Build) -> list[str]:
        assert instance is not None
        return await _install_and_launch(build, instance, ctx, config)

    build = await runner.start(
        ctx.scope,
        kind=kind,
        folder=folder,
        udid=udid,
        developer_dir=instance.developer_dir if instance else config.developer_dir,
        device=device,
        configuration=args.get("configuration") or config.build_configuration or None,
        timeout_s=config.build_timeout_minutes * 60,
        scheme=args.get("scheme"),
        project=args.get("project"),
        workspace=args.get("workspace"),
        only_testing=args.get("only_testing"),
        skip_testing=args.get("skip_testing"),
        test_plan=args.get("test_plan"),
        retries=whole_arg(args.get("retries"), 0, TEST_RETRIES_BOUNDS, "retries"),
        warnings=flag_arg(args.get("warnings"), "warnings"),
        test_diagnostics=config.build_test_diagnostics,
        team=await _team(ctx, config) if is_device_udid(udid) else "",
        after=after if kind == "build" else None,
    )
    if kind == "test" and instance is not None:
        _keep_busy(ctx.manager, instance, build)
    return text("\n".join([*notes, await runner.result(ctx.scope.id, build.id, wait_s)]))


async def _build_run(args: dict[str, Any], ctx: ToolContext) -> Result:
    return await _run(args, ctx, "build")


async def _test(args: dict[str, Any], ctx: ToolContext) -> Result:
    return await _run(args, ctx, "test")


class BuildToolProvider:
    """The build tools: listed while ``build.tools`` is on; otherwise the agent is told to build from its shell."""

    tools: tuple[Tool, ...] = (make_tool("sim_build_run", NEEDS, _build_run), make_tool("sim_test", NEEDS, _test))

    def offered(self, config: SimConfig) -> bool:
        return config.build_tools

    def instructions(self, config: SimConfig) -> str:
        return BUILD_INSTRUCTIONS if config.build_tools else SHELL_INSTRUCTIONS
