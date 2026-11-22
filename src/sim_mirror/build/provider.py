# SPDX-License-Identifier: Apache-2.0
"""The build tools -- `sim_build_run` and `sim_test` -- listed while a scope's ``build.tools`` is on.

A build runs xcodebuild, which is running commands: the tools are refused on every call unless the host also lets the
scope run commands (`Policy.shells_allowed`). A build that succeeded is installed and launched on the calling agent's
device, drawn for anyone watching. While a build runs, until its app is installed and launched, and while tests run,
the device is busy: it takes no agent gestures, nor a `sim_device` choose, restart or stop that would take it away,
and every screen watching is told why.
A build of another app than the scope's ``build.expected_bundle_id`` is not installed: a configuration with a bundle
id of its own would put a second app beside the one a person uses. Nor is a test run whose tests run in another app
started, since xcodebuild would install it.

A test run may be sent to another simulator on this Mac instead (`destination`). Not to one that is somebody else's:
another scope's running device, one another live agent of this scope works on, one another scope's tests are running
on, or one another process on the Mac has claimed. A device of this scope's whose agent has gone quiet is free to
use, and is kept busy while the tests run on it. xcodebuild boots a device if it has to, and it is left running, as
Xcode leaves it.

A run builds the folder the agent works in (`build.checkouts`): the one it names in ``folder`` -- kept for its later
calls (`build.folders`) -- else the one it named before, else the first folder its client works in (its MCP roots)
that this scope may build -- the build folder, or the same folder of another checkout of its repository, such as a git
worktree -- else the build folder, and a client working somewhere else is told so. A build in another folder than the
build folder runs with that folder's own settings, when a person gave it some.

Every checkout builds in the scope's one DerivedData folder, one run at a time, unless ``build.derived_data`` is
``checkout``: then each checkout has a folder of its own, and builds of two worktrees run side by side
(`build.derived`). A folder per worktree is gigabytes for each, with every Swift package resolved and built again, so
it is a choice, and the folder of a worktree that is gone is deleted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sim_mirror.build.checkouts import FOLDER_MAX, build_folder_for
from sim_mirror.build.derived import checkout_folder, checkout_top, mark, prune
from sim_mirror.build.destination import ANDROID, SIMULATORS, choose_destination, described
from sim_mirror.build.stamp import discard
from sim_mirror.build.xcodebuild import Build, BuildRefused, BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import ActionError
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager, SimulatorUnavailable
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import is_device_udid
from sim_mirror.protocol import DeviceKind
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


def kept_folder(ctx: ToolContext, home: Path) -> Path | None:
    """The folder the calling agent named for its builds before (`build.folders`) -- `home` itself when it is that --
    while it is still one this scope may build; None when it named none, and forgotten once it is gone."""
    if ctx.folders is None:
        return None
    kept = ctx.folders.of(ctx.caller)
    if kept is None:
        return None
    found = build_folder_for(kept, home, exact=True)
    if found is None or not found.is_dir():
        ctx.folders.drop(ctx.caller)
        return None
    return home if found == home.resolve() else found


def working_folder(args: dict[str, Any], ctx: ToolContext, home: Path) -> tuple[Path, list[str]]:
    """The folder a run builds in -- `home` itself when it is that -- and a note when the agent's client works where
    this scope may not build. A folder the call names is kept for the agent's later calls."""
    if args.get("folder") is not None:
        chosen = _named_folder(args["folder"], home)
        if ctx.folders is not None:
            ctx.folders.keep(ctx.caller, chosen)
        return home if chosen == home.resolve() else chosen, []
    kept = kept_folder(ctx, home)
    if kept is not None:
        return kept, []
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


async def _derived(
    runner: BuildRunner, ctx: ToolContext, config: SimConfig, folder: Path, home: Path, kind: str
) -> Path | None:
    """The DerivedData folder a run of `folder` builds in: None for the scope's own, or -- ``build.derived_data =
    "checkout"`` -- its checkout's, beside it (`build.derived`). The scope's folders of checkouts that are gone are
    deleted first, but any a run is using; an Android build keeps its output in its project, so no folder is made."""
    if config.build_derived_data != "checkout":
        return None
    scope_folder = runner.derived_data(ctx.scope)
    top = checkout_top(folder, home)
    derived = checkout_folder(scope_folder, top)
    using = {run.lane for run in runner.runs() if run.lane is not None} | {derived}
    await asyncio.to_thread(prune, scope_folder, using)
    if kind != "android":
        try:
            await asyncio.to_thread(mark, derived, top)
        except OSError as exc:
            raise ToolRefused(
                f"could not make {derived}, the DerivedData folder of {top}: {exc.strerror or exc}"
            ) from exc
    return derived


async def _install_and_launch(build: Build, instance: DeviceInstance, ctx: ToolContext, config: SimConfig) -> list[str]:
    """Put a build that succeeded on the device and open it, drawn for anyone watching. `config` is the settings of
    the folder built, which a worktree may have of its own."""
    if (build.app is None or build.bundle_id is None) and instance.kind == "android":
        raise BuildRefused(
            f"{build.scheme} ({build.configuration}) left no APK its output-metadata.json names, so there is nothing "
            "to install; the log says what Gradle built"
        )
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
        build.installed = True
        launching = control.launch(instance.udid, build.bundle_id)
        pid = await ctx.actions.announced(instance, ctx.caller, "app", f"launch {build.bundle_id}", launching)
        build.launched = True
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


def _doing(kind: str) -> str:
    return "running tests" if kind == "test" else "building"


def _hold(instance: DeviceInstance | None, kind: str) -> bool:
    """Mark a device busy while its run is made ready -- before the runner has given it an id -- unless something
    already has it busy. Answers whether it was marked."""
    if instance is None or instance.busy is not None:
        return False
    instance.busy = _doing(kind)
    return True


def _release(instance: DeviceInstance | None, kind: str) -> None:
    """Free a device marked busy for a run that was not started, unless something else has marked it since."""
    if instance is not None and instance.busy == _doing(kind):
        instance.busy = None


def _keep_busy(manager: DeviceManager, instance: DeviceInstance, build: Build) -> None:
    """A device is busy while a run is going on it: tests drive it themselves, and an agent's gestures would land in
    the middle of them; a build ends by installing and launching its app on it, which a device switched or restarted
    under it would lose.

    Every screen watching is told what the device is busy with, so a person can see why it is not theirs to touch.
    """
    busy = f"{_doing(build.kind)} ({build.id})"
    instance.busy = busy
    manager.publish_status(instance)

    def free(_: object) -> None:
        if instance.busy == busy:
            instance.busy = None
            manager.publish_status(instance)

    assert build.task is not None
    build.task.add_done_callback(free)


async def _elsewhere(
    ctx: ToolContext, runner: BuildRunner, value: object, own_kind: DeviceKind
) -> tuple[str, str, DeviceKind] | None:
    """The device a test run's ``destination`` names, how its answer names it, and its kind -- None for the scope's
    own device -- or why it is not this scope's to use. Only a device the scope's own toolchain builds for is one."""
    try:
        listed = await ctx.manager.devices(ctx.scope)
    except SimulatorUnavailable as exc:
        raise ToolRefused(str(exc)) from exc
    toolchain = runner.toolchain(own_kind)
    # Gradle runs tests only on a device that is running: one it can reach through adb.
    choices = [
        choice
        for choice in listed
        if runner.builds_for(choice["kind"]) is toolchain and (own_kind != "android" or choice["state"] != "Shutdown")
    ]
    choice = choose_destination(choices, value, ANDROID if own_kind == "android" else SIMULATORS)
    udid, named = choice["udid"], described(choice)
    own = ctx.instance()
    if own is not None and own.udid == udid:
        return None
    noun = ctx.copy.scope_noun
    device = "device" if choice["kind"] == "android" else "simulator"
    running = ctx.manager.running(udid)
    if running is not None and ctx.scope.id not in running.scopes:
        raise BuildRefused(f"{named} is another {noun}'s {device} here; name one nobody is using")
    # One of this scope's devices is free to use once the agent working on it has gone quiet.
    working = ctx.others_on(udid)
    if working:
        quiet = ctx.selections_quiet(working[0])
        raise BuildRefused(
            f"{named} is the {device} {working[0].title} works on (last call {quiet} s ago); name one nobody is using"
        )
    if running is not None and running.busy:
        raise BuildRefused(f"{named} is busy: {running.busy}; name another {device}")
    if any(build.udid == udid and build.scope.id != ctx.scope.id for build in runner.runs()):
        raise BuildRefused(f"another {noun}'s tests are running on {named}; name another {device}")
    claim = await ctx.manager.holder(udid)
    if claim is not None:
        raise BuildRefused(ctx.copy.claimed_of_kind(claim.owner, claim.pid, choice["kind"]))
    return udid, named, choice["kind"]


async def _team(ctx: ToolContext, config: SimConfig, folder: Path) -> str:
    """The team that signs a build of `folder` for a real device when its project names none (`core.signing`): found
    from the folder built, which a worktree's branch may sign differently, not from the build folder."""
    if ctx.signing is None:
        return config.real_devices_team_id
    found = await ctx.signing.of(ctx.scope, config, folder=folder)
    return found.team if found else ""


async def _run(args: dict[str, Any], ctx: ToolContext, kind: str) -> Result:
    runner, home = _builds(ctx)
    wait_s = whole_arg(args.get("wait_s"), BUILD_WAIT_S, BUILD_WAIT_BOUNDS, "wait_s")
    if args.get("build_id") is not None:
        return await _result(runner, ctx.scope.id, args["build_id"], wait_s)
    folder, notes = working_folder(args, ctx, home)
    config = ctx.settings_for(folder) if ctx.settings_for is not None and folder != home else ctx.config
    if kind == "build" and args.get("destination") is not None:
        raise ToolRefused("destination is for sim_test: a build is installed and launched on your own simulator")
    # Before the device is brought up: a kind nothing here builds for is refused without booting it.
    own = ctx.manager.kind(ctx.scope, ctx.udid)
    runner.toolchain(own)
    elsewhere = await _elsewhere(ctx, runner, args["destination"], own) if args.get("destination") is not None else None
    instance: DeviceInstance | None = None
    if elsewhere is not None:
        udid, device, device_kind = elsewhere
        # A device of this scope's that its quiet agent left running takes no gestures while the tests run on it.
        target = ctx.manager.instance(ctx.scope, udid)
    else:
        instance = await ready_device(ctx)
        udid, device, device_kind = instance.udid, "", instance.kind
        target = instance

    async def after(build: Build) -> list[str]:
        assert instance is not None
        return await _install_and_launch(build, instance, ctx, config)

    derived = await _derived(runner, ctx, config, folder, home, device_kind)
    held = _hold(target, kind)
    try:
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
            team=await _team(ctx, config, folder) if is_device_udid(udid) else "",
            after=after if kind == "build" else None,
            # A build is held to it once built, before SimMirror installs it; a test host xcodebuild installs itself.
            expected_bundle_id=config.build_expected_bundle_id if kind == "test" else "",
            device_kind=device_kind,
            java_home=config.android_java_home,
            catalyst=flag_arg(args.get("catalyst"), "catalyst"),
            derived=derived,
        )
    except BaseException:
        if held:
            _release(target, kind)
        raise
    if held:
        assert target is not None
        _keep_busy(ctx.manager, target, build)
    return await _result(runner, ctx.scope.id, build.id, wait_s, notes)


async def _result(
    runner: BuildRunner, scope_id: str, build_id: object, wait_s: float, notes: Sequence[str] = ()
) -> Result:
    answer, data = await runner.report(scope_id, build_id, wait_s)
    failed = data["state"] != "running" and (
        data["state"] != "succeeded" or (data["kind"] == "build" and not data["launched"])
    )
    return text("\n".join([*notes, answer]), error=failed, data=data)


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
