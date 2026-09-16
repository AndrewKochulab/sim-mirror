# SPDX-License-Identifier: Apache-2.0
"""The build tools -- `sim_build_run` and `sim_test` -- a preview, listed while a scope's ``build.tools`` is on.

A build runs xcodebuild, which is running commands: the tools are refused on every call unless the host also lets the
scope run commands (`Policy.shells_allowed`). A build that succeeded is installed and launched on the scope's device,
drawn for anyone watching; while tests run the device takes no agent gestures, and every screen watching is told why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sim_mirror.build.xcodebuild import Build, BuildRefused, BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability
from sim_mirror.core.actions import ActionError
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.tools.context import Tool, ToolContext, make_tool, ready_device, whole_arg
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
    if ctx.builds is None or ctx.folder is None:
        raise ToolRefused("The build runner is not running.")
    return ctx.builds, ctx.folder


async def _install_and_launch(build: Build, instance: DeviceInstance, ctx: ToolContext) -> list[str]:
    """Put a build that succeeded on the device and open it, drawn for anyone watching."""
    if build.app is None or build.bundle_id is None:
        raise BuildRefused("the build settings name no app to install")
    simctl = ctx.manager.simctl(instance)
    try:
        installing = simctl.install(instance.udid, str(build.app))
        await ctx.actions.announced(instance, ctx.caller, "app", f"install {build.app.name}", installing)
        launching = simctl.launch(instance.udid, build.bundle_id)
        pid = await ctx.actions.announced(instance, ctx.caller, "app", f"launch {build.bundle_id}", launching)
    except (SimctlError, ActionError) as exc:
        raise BuildRefused(str(exc)) from exc
    if pid is not None:
        instance.launched[build.bundle_id] = pid
    return [f"installed {build.app.name}", f"launched {build.bundle_id}" + (f" (pid {pid})" if pid else "")]


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


async def _run(args: dict[str, Any], ctx: ToolContext, kind: str) -> Result:
    runner, folder = _builds(ctx)
    wait_s = whole_arg(args.get("wait_s"), BUILD_WAIT_S, BUILD_WAIT_BOUNDS, "wait_s")
    if args.get("build_id") is not None:
        return text(await runner.result(ctx.scope.id, args["build_id"], wait_s))
    instance = await ready_device(ctx)

    async def after(build: Build) -> list[str]:
        return await _install_and_launch(build, instance, ctx)

    build = await runner.start(
        ctx.scope,
        kind=kind,
        folder=folder,
        udid=instance.udid,
        developer_dir=instance.developer_dir,
        configuration=args.get("configuration") or ctx.config.build_configuration,
        timeout_s=ctx.config.build_timeout_minutes * 60,
        scheme=args.get("scheme"),
        project=args.get("project"),
        workspace=args.get("workspace"),
        only_testing=args.get("only_testing"),
        skip_testing=args.get("skip_testing"),
        test_plan=args.get("test_plan"),
        retries=whole_arg(args.get("retries"), 0, TEST_RETRIES_BOUNDS, "retries"),
        after=after if kind == "build" else None,
    )
    if kind == "test":
        _keep_busy(ctx.manager, instance, build)
    return text(await runner.result(ctx.scope.id, build.id, wait_s))


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
