# SPDX-License-Identifier: Apache-2.0
"""Building and testing a scope's app on the scope's device, with an answer an agent can read.

`sim_build_run` and `sim_test` (`build/provider.py`) come here. A build is

    xcrun xcodebuild (-workspace W | -project P) -scheme S -configuration C
        -destination platform=iOS Simulator,id=<udid>
        -derivedDataPath <StateStore.derived_data(scope)>
        -resultBundlePath <StateStore.builds_dir(scope)>/<stamp>-<id>.xcresult
        build | test

with its output written to a log beside the bundle, and its answer read from the bundle (`xcresult.py`) -- never
scraped from the log, whose format is Xcode's to change. A built app's path and bundle id come from
``-showBuildSettings``, so it can be installed and launched straight after.

**Which project**: the one the build folder holds -- a workspace before the project inside it -- or the one named,
which must be inside that folder. A folder with several asks the agent to name one; a project with several schemes
asks for a scheme.

**One at a time**: a scope runs one build or test run at a time. A run longer than its timeout is ended with its
process group, and so is one cancelled. A call waits up to `wait_s`; a build still going answers with its id, and a
later call with that id picks it up -- so no tool call has to outlive an MCP client's timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.build import xcresult
from sim_mirror.platform.process import signal_group as signal_process_group
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun, start_xcrun
from sim_mirror.scope import Scope
from sim_mirror.seams import StateStore

#: Xcode's own DerivedData, where an app built outside SimMirror usually is.
DERIVED_DATA = Path("~/Library/Developer/Xcode/DerivedData").expanduser()
KINDS = ("build", "test")
LIST_TIMEOUT_S = 60.0
SETTINGS_TIMEOUT_S = 120.0
RESULT_TIMEOUT_S = 60.0
STOP_GRACE_S = 5.0
TESTS_MAX = 50
#: The longest project or workspace name a call may give.
PATH_MAX = 500

_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 _.+-]{0,127}\Z")
_TEST_ID = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_./()-]{0,299}\Z")
#: A workspace's references to the projects it builds, relative to the folder the workspace is in.
_PROJECT_REF = re.compile(r'location\s*=\s*"(?:group|container):([^"]+\.xcodeproj)"')
#: Folders build settings never come from, left out when looking for `.xcconfig` files.
_NOT_SETTINGS = frozenset({"DerivedData", "build", "node_modules"})


class BuildRefused(Exception):
    """A build that will not be started or read, said so the agent knows what to do instead."""


class Starter(Protocol):
    """How a long xcrun call is started; `xcrun.start_xcrun`, or a fake."""

    async def __call__(self, *args: str, log_path: Path, developer_dir: str = ..., cwd: Path | None = ...) -> Any:
        """Start ``xcrun <args>`` in its own process group, its output appended to `log_path`."""
        ...


@dataclass(frozen=True)
class Project:
    flag: str
    path: Path

    @property
    def args(self) -> tuple[str, str]:
        return self.flag, str(self.path)


def _named(value: object, what: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise BuildRefused(f"{what} must be a name like Debug or MyApp, not {value!r}")
    return value


def _test_ids(values: object, what: str) -> list[str]:
    if values is None:
        return []
    if not (
        isinstance(values, list)
        and len(values) <= TESTS_MAX
        and all(isinstance(value, str) and _TEST_ID.match(value) for value in values)
    ):
        raise BuildRefused(
            f"{what} is a list of at most {TESTS_MAX} test identifiers, like AppTests/LoginTests/testLogin"
        )
    return values


def _stamp(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _schemes(folder: Path) -> list[tuple[str, int]]:
    try:
        return sorted((str(path), _stamp(path)) for path in folder.rglob("*.xcscheme"))
    except OSError:
        return []


def _configs(folder: Path) -> list[tuple[str, int]]:
    """Every `.xcconfig` under ``folder`` -- where a bundle id or product name can be set -- and when it changed."""
    found: list[tuple[str, int]] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if name not in _NOT_SETTINGS and not name.startswith("."))
        found += [
            (os.path.join(root, name), _stamp(Path(root) / name))
            for name in sorted(files)
            if name.endswith(".xcconfig")
        ]
    return found


def changed(project: Project) -> tuple[Any, ...]:
    """How a project stands in the ways its schemes or its app could change.

    Its file and every scheme in it: schemes live in `xcshareddata` or a person's own `xcuserdata`, levels below the
    project, so each is looked at. A workspace holds none of that itself, so each project it names is looked at the
    same way; and so is every `.xcconfig` in the folder the project or workspace is in, where build settings such as
    a bundle id live. A project's folder is small (DerivedData is elsewhere, and build output is skipped).
    """
    stamps: list[Any] = [
        _stamp(project.path / "project.pbxproj"),
        _stamp(project.path / "contents.xcworkspacedata"),
        *_schemes(project.path),
    ]
    if project.path.suffix == ".xcworkspace":
        try:
            contents = (project.path / "contents.xcworkspacedata").read_text(encoding="utf-8", errors="replace")
        except OSError:
            contents = ""
        for location in _PROJECT_REF.findall(contents):
            named = project.path.parent / location
            stamps += [(str(named), _stamp(named / "project.pbxproj")), *_schemes(named)]
    return (*stamps, *_configs(project.path.parent))


def find_project(folder: Path, *, project: object = None, workspace: object = None) -> Project:
    """The project or workspace a build uses, from the build folder or as named."""
    for value, what in ((project, "project"), (workspace, "workspace")):
        if value is not None and (not isinstance(value, str) or "\x00" in value or len(value) > PATH_MAX):
            raise BuildRefused(f"{what} is the name of an Xcode {what} in {folder}")
    if project and workspace:
        raise BuildRefused("name a project or a workspace, not both")
    named = project or workspace
    if isinstance(named, str) and named:
        flag, suffix = ("-project", ".xcodeproj") if project else ("-workspace", ".xcworkspace")
        path = (folder / named).resolve()
        if path.suffix != suffix or not path.is_dir():
            raise BuildRefused(f"{named} is not an Xcode {flag[1:]} in {folder}")
        if not path.is_relative_to(folder.resolve()):
            raise BuildRefused(f"the {flag[1:]} must be inside {folder}")
        return Project(flag, path)
    workspaces = sorted(path for path in folder.glob("*.xcworkspace") if path.is_dir())
    projects = sorted(path for path in folder.glob("*.xcodeproj") if path.is_dir())
    found = workspaces or projects
    if len(found) == 1:
        return Project("-workspace" if workspaces else "-project", found[0])
    if not found:
        raise BuildRefused(f"there is no Xcode project or workspace in {folder}; name one with project or workspace")
    raise BuildRefused("this folder has several; name one: " + ", ".join(path.name for path in found))


@dataclass
class Build:
    id: str
    scope: Scope
    kind: str
    project: Project
    scheme: str
    configuration: str
    udid: str
    developer_dir: str
    folder: Path
    derived: Path
    bundle: Path
    log: Path
    started: float
    process: Any = None
    state: str = "running"
    answer: str = ""
    app: Path | None = None
    bundle_id: str | None = None
    task: asyncio.Task[None] | None = None

    @property
    def label(self) -> str:
        return f"{self.scheme} ({self.configuration})"


#: Run after a build that succeeded, with the build; answers with the lines to add (install, launch).
After = Callable[[Build], Awaitable[list[str]]]


#: How many finished runs a scope keeps readable by build_id; older ones are let go.
KEEP_FINISHED = 20


class BuildRunner:
    """Every scope's builds and test runs, for one process."""

    def __init__(
        self,
        state: StateStore,
        *,
        xcrun: XcrunRunner = run_xcrun,
        start: Starter = start_xcrun,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        signal_group: Callable[[int, int], None] = signal_process_group,
        stop_grace_s: float = STOP_GRACE_S,
    ) -> None:
        self._state = state
        self._xcrun = xcrun
        self._start = start
        self._clock = clock
        self._wall = wall
        self._signal = signal_group
        self._stop_grace_s = stop_grace_s
        self._builds: dict[str, Build] = {}
        self._running: dict[str, Build] = {}
        #: What `-list` and `-showBuildSettings` answered, kept while the project is unchanged: each takes seconds,
        #: and an up-to-date rebuild that spends ten of its eleven seconds asking them again is the agent waiting.
        self._schemes: dict[tuple[str, str], tuple[tuple[Any, ...], list[str]]] = {}
        self._apps: dict[tuple[str, ...], tuple[tuple[Any, ...], Path, str | None]] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    def running(self, scope_id: str) -> Build | None:
        return self._running.get(scope_id)

    def runs(self) -> list[Build]:
        """Every build or test run still going."""
        return list(self._running.values())

    async def start(
        self,
        scope: Scope,
        *,
        kind: str,
        folder: Path,
        udid: str,
        developer_dir: str,
        configuration: object,
        timeout_s: float,
        scheme: object = None,
        project: object = None,
        workspace: object = None,
        only_testing: object = None,
        skip_testing: object = None,
        after: After | None = None,
    ) -> Build:
        """Start a build or a test run for a scope. Refuses with a reason, never runs two at once for one scope."""
        if kind not in KINDS:
            raise BuildRefused(f"a run is one of {', '.join(KINDS)}")
        async with self._lock:
            current = self._running.get(scope.id)
            if current is not None:
                raise BuildRefused(
                    f"{current.kind} {current.id} is still running here; wait for it with build_id {current.id}"
                )
            target = find_project(folder, project=project, workspace=workspace)
            chosen_configuration = _named(configuration, "configuration")
            only, skip = _test_ids(only_testing, "only_testing"), _test_ids(skip_testing, "skip_testing")
            chosen = await self._scheme(target, scheme, developer_dir, folder)
            build_id = f"b{next(self._ids)}"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._wall()))
            bundle = self._state.ensure_dir(self._state.builds_dir(scope)) / f"{stamp}-{build_id}.xcresult"
            build = Build(
                id=build_id,
                scope=scope,
                kind=kind,
                project=target,
                scheme=chosen,
                configuration=chosen_configuration,
                udid=udid,
                developer_dir=developer_dir,
                folder=folder,
                derived=self._state.derived_data(scope),
                bundle=bundle,
                log=bundle.with_suffix(".log"),
                started=self._clock(),
            )
            argv = [
                "xcodebuild",
                *target.args,
                "-scheme",
                chosen,
                "-configuration",
                chosen_configuration,
                "-destination",
                f"platform=iOS Simulator,id={udid}",
                "-derivedDataPath",
                str(build.derived),
                "-resultBundlePath",
                str(bundle),
                *(f"-only-testing:{test}" for test in only),
                *(f"-skip-testing:{test}" for test in skip),
                kind,
            ]
            try:
                build.process = await self._start(*argv, log_path=build.log, developer_dir=developer_dir, cwd=folder)
            except OSError as exc:
                raise BuildRefused(f"xcodebuild could not be started: {exc}") from exc
            self._forget_finished(scope.id)
            self._builds[build_id] = build
            self._running[scope.id] = build
            build.task = asyncio.get_running_loop().create_task(self._watch(build, timeout_s, after))
            return build

    def _forget_finished(self, scope_id: str) -> None:
        """Keep a scope's last `KEEP_FINISHED` finished runs readable by id, and let go of the rest."""
        finished = [
            build
            for build in self._builds.values()
            if build.scope.id == scope_id and build.task is not None and build.task.done()
        ]
        if len(finished) > KEEP_FINISHED:
            for build in finished[: len(finished) - KEEP_FINISHED]:
                del self._builds[build.id]

    async def _scheme(self, target: Project, scheme: object, developer_dir: str, folder: Path) -> str:
        stamp = changed(target)
        known = self._schemes.get((str(target.path), developer_dir))
        if known is not None and known[0] == stamp:
            schemes = known[1]
        else:
            listed = await self._json(
                ("xcodebuild", "-list", "-json", *target.args),
                developer_dir,
                folder,
                LIST_TIMEOUT_S,
                refuse=f"xcodebuild could not list {target.path.name}",
            )
            body = (listed.get("workspace") or listed.get("project")) if isinstance(listed, dict) else None
            names = body.get("schemes") if isinstance(body, dict) else None
            schemes = [name for name in names or [] if isinstance(name, str)]
            self._schemes[(str(target.path), developer_dir)] = (stamp, schemes)
        if scheme is not None:
            name = _named(scheme, "scheme")
            if name not in schemes:
                raise BuildRefused(f"{target.path.name} has no scheme {name}; it has {', '.join(schemes) or 'none'}")
            return name
        if len(schemes) == 1:
            return schemes[0]
        if not schemes:
            raise BuildRefused(f"{target.path.name} has no schemes to build")
        raise BuildRefused(f"{target.path.name} has several schemes; name one: {', '.join(schemes)}")

    async def _json(
        self, args: Sequence[str], developer_dir: str, folder: Path | None, timeout: float, *, refuse: str | None = None
    ) -> Any:
        """What an xcrun call answered as JSON -- or, when it did not, a refusal (or None when not refusing)."""
        result = await self._xcrun(*args, timeout=timeout, developer_dir=developer_dir, cwd=folder)
        try:
            document = json.loads(result.out) if result.ok else None
        except ValueError:
            document = None
        if document is None and refuse is not None:
            raise BuildRefused(f"{refuse}: {result.message}")
        return document

    async def _watch(self, build: Build, timeout_s: float, after: After | None) -> None:
        try:
            try:
                await asyncio.wait_for(build.process.wait(), timeout=timeout_s)
            except (asyncio.TimeoutError, TimeoutError):
                await self._end(build)
                build.state = "timed_out"
                build.answer = f"{build.kind} {build.id} was stopped after {round(timeout_s)}s\nlog {build.log}"
                return
            lines = await self._results(build)
            if build.state == "succeeded" and build.kind == "build" and after is not None:
                try:
                    lines += await after(build)
                except BuildRefused as exc:
                    lines.append(f"not launched: {exc}")
            build.answer = "\n".join([*lines, f"log {build.log}"])
        except asyncio.CancelledError:
            await self._end(build)
            build.state = "cancelled"
            build.answer = f"{build.kind} {build.id} was cancelled\nlog {build.log}"
            raise
        finally:
            self._running.pop(build.scope.id, None)

    async def _results(self, build: Build) -> list[str]:
        bundle = str(build.bundle)
        if build.kind == "build":
            document = await self._json(
                ("xcresulttool", "get", "build-results", "--path", bundle), build.developer_dir, None, RESULT_TIMEOUT_S
            )
            if not isinstance(document, dict):
                build.state = "failed"
                return [f"build {build.id} ended without a result to read ({build.label}); the log says why"]
            summary = xcresult.build_summary(document)
            build.state = "succeeded" if summary.succeeded else "failed"
            if summary.succeeded:
                build.app, build.bundle_id = await self._built_app(build)
            return xcresult.render_build(summary, label=build.label, root=build.folder)
        report = await self._json(
            ("xcresulttool", "get", "test-results", "summary", "--path", bundle),
            build.developer_dir,
            None,
            RESULT_TIMEOUT_S,
        )
        if not isinstance(report, dict):
            build.state = "failed"
            return [f"test {build.id} ended without a result to read ({build.label}); the log says why"]
        tests = await self._json(
            ("xcresulttool", "get", "test-results", "tests", "--path", bundle),
            build.developer_dir,
            None,
            RESULT_TIMEOUT_S,
        )
        summary_of_tests = xcresult.suite_summary(report, tests if isinstance(tests, dict) else None)
        build.state = "succeeded" if summary_of_tests.passed else "failed"
        return xcresult.render_tests(summary_of_tests, label=build.label)

    async def _built_app(self, build: Build) -> tuple[Path | None, str | None]:
        """Where the built app is and what it is called, from the build settings of its application target.

        Kept for the next build of the same scheme and configuration while the project is unchanged and the app is
        still where they said.
        """
        key = (str(build.project.path), build.developer_dir, build.scheme, build.configuration, str(build.derived))
        stamp = changed(build.project)
        known = self._apps.get(key)
        if known is not None and known[0] == stamp and known[1].exists():
            return known[1], known[2]
        app, bundle_id = await self._read_built_app(build)
        if app is not None:
            self._apps[key] = (stamp, app, bundle_id)
        return app, bundle_id

    async def _read_built_app(self, build: Build) -> tuple[Path | None, str | None]:
        settings = await self._json(
            (
                "xcodebuild",
                "-showBuildSettings",
                "-json",
                *build.project.args,
                "-scheme",
                build.scheme,
                "-configuration",
                build.configuration,
                "-destination",
                f"platform=iOS Simulator,id={build.udid}",
                "-derivedDataPath",
                str(build.derived),
            ),
            build.developer_dir,
            build.folder,
            SETTINGS_TIMEOUT_S,
        )
        for entry in settings if isinstance(settings, list) else []:
            values = entry.get("buildSettings") if isinstance(entry, dict) else None
            if not isinstance(values, dict):
                continue
            wrapper = values.get("WRAPPER_NAME")
            if isinstance(wrapper, str) and wrapper.endswith(".app") and values.get("TARGET_BUILD_DIR"):
                return Path(values["TARGET_BUILD_DIR"]) / wrapper, values.get("PRODUCT_BUNDLE_IDENTIFIER")
        return None, None

    async def _end(self, build: Build) -> None:
        """End a build and everything it started: TERM to its group, then KILL if it does not go."""
        process = build.process
        if process is None or process.returncode is not None:
            return
        self._signal(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=self._stop_grace_s)
        except (asyncio.TimeoutError, TimeoutError):
            self._signal(process.pid, signal.SIGKILL)

    async def result(self, scope_id: str, build_id: object, wait_s: float) -> str:
        """A scope's build's answer, waiting up to `wait_s` for it -- or how long it has been running.

        Another scope's build is not there to be read: its errors name that scope's files.
        """
        build = self._builds.get(build_id) if isinstance(build_id, str) else None
        if build is None or build.scope.id != scope_id:
            raise BuildRefused(f"there is no build {build_id!r}")
        if build.task is not None and not build.task.done():
            await asyncio.wait({build.task}, timeout=wait_s)
        if build.task is not None and not build.task.done():
            return (
                f"{build.kind} {build.id} still running ({round(self._clock() - build.started)}s) · "
                f"call again with build_id {build.id}"
            )
        return build.answer

    async def cancel(self, scope_id: str) -> bool:
        """End a scope's running build, if it has one."""
        build = self._running.get(scope_id)
        if build is None or build.task is None:
            return False
        build.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await build.task
        return True

    async def cancel_group(self, group: str) -> int:
        """End every run a group of scopes has going -- its simulator was switched off. Answers how many."""
        scope_ids = [scope_id for scope_id, build in list(self._running.items()) if build.scope.group == group]
        return sum([await self.cancel(scope_id) for scope_id in scope_ids])

    async def shutdown(self) -> None:
        for scope_id in list(self._running):
            await self.cancel(scope_id)
