# SPDX-License-Identifier: Apache-2.0
"""Running a scope's builds and test runs, one at a time, whatever builds them.

What builds a run is a `Toolchain`: xcodebuild for a simulator or an iPhone (`xcodebuild.XcodeToolchain`), Gradle for an
Android device (`gradle.GradleToolchain`), chosen by the kind of device the run is for. A toolchain finds the project,
says which run a call asks for (`Toolchain.prepare`), starts it (`Toolchain.launch`) and reads its answer
(`Toolchain.results`); everything around that is the same for each and is here:

**One at a time**: a scope runs one build or test run at a time. A run longer than its timeout is ended with its
process group, and so is one cancelled. A call waits up to `wait_s`; a build still going answers with its id, and a
later call with that id picks it up -- so no tool call has to outlive an MCP client's timeout. The answer and the head
of the log name the source built: the folder, and its branch and commit (`source.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import signal
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.build.source import Source, SourceReader, read_source
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.process import signal_group as signal_process_group
from sim_mirror.protocol import DeviceKind
from sim_mirror.scope import Scope
from sim_mirror.seams import StateStore

KINDS = ("build", "test")
STOP_GRACE_S = 5.0
TESTS_MAX = 50
TEST_ID_MAX = 300
#: How many finished runs a scope keeps readable by build_id; older ones are let go.
KEEP_FINISHED = 20
#: How many of a scope's runs a refusal for an unknown build_id names.
RECENT_LISTED = 5


class BuildRefused(Exception):
    """A build that will not be started or read, said so the agent knows what to do instead."""


def _is_test_id(value: object) -> bool:
    """A test as a failure names it: ``ParsingTests/withArguments(value:)`` has a colon, and a target may have spaces.
    Each reaches the build tool inside one argument, so only a line break or a stray space could make it name something
    else."""
    return isinstance(value, str) and 0 < len(value) <= TEST_ID_MAX and value.isprintable() and value == value.strip()


def test_ids(values: object, what: str, example: str) -> list[str]:
    """The tests a call's `only_testing` or `skip_testing` names -- or why not, with `example` of how they are named."""
    if values is None:
        return []
    if not (isinstance(values, list) and len(values) <= TESTS_MAX and all(_is_test_id(value) for value in values)):
        raise BuildRefused(f"{what} is a list of at most {TESTS_MAX} tests as a failure names them: {example}")
    return values


@dataclass(frozen=True)
class Request:
    """What a call asks to be built or tested, as the tool took it: the toolchain says what each part means to it."""

    kind: str
    folder: Path
    udid: str
    developer_dir: str
    timeout_s: float
    configuration: object = None
    scheme: object = None
    project: object = None
    workspace: object = None
    only_testing: object = None
    skip_testing: object = None
    test_plan: object = None
    retries: int = 0
    warnings: bool = False
    test_diagnostics: bool = False
    #: The device a test run was sent to, when it is not the scope's own; "" when it is.
    device: str = ""
    #: The team that signs a build for a real iPhone when its project names none.
    team: str = ""
    expected_bundle_id: str = ""
    #: The JDK a Gradle build runs with, as ``android.java_home`` names it; "" to find one.
    java_home: str = ""


@dataclass(frozen=True)
class Project:
    """What a run builds: an Xcode project or workspace, as the flag that names it to xcodebuild -- or a Gradle build,
    by its settings file."""

    flag: str
    path: Path

    @property
    def args(self) -> tuple[str, str]:
        return self.flag, str(self.path)


@dataclass
class Build:
    """One build or test run of a scope's, from the call that started it to the answer it gave."""

    id: str
    scope: Scope
    kind: str
    project: Project
    #: What is built: an Xcode scheme, or a Gradle application module.
    scheme: str
    #: How: an Xcode build configuration, or a Gradle build variant.
    configuration: str
    udid: str
    developer_dir: str
    folder: Path
    #: Where the build's intermediate products go.
    derived: Path
    #: Where the run's results are written: an xcresult bundle, or a folder Gradle's reports are kept in.
    bundle: Path
    log: Path
    started: float
    #: Whether the configuration is the one the scheme's own action builds with, as no call or setting named one.
    configuration_from_scheme: bool = False
    #: The scheme's test plan this run names, or "" when the scheme has none and Xcode uses its own test action.
    test_plan: str = ""
    #: Every scheme (or module) the project has, for a refusal that has to point at another.
    schemes: tuple[str, ...] = ()
    #: Whether the answer lists warnings as well as errors.
    warnings: bool = False
    #: The device a test run was sent to, when it is not the scope's own device; "" when it is.
    device: str = ""
    #: What signs a build for a real device: leave to register it and make its profile, and a team when the project
    #: names none.
    signing: tuple[str, ...] = ()
    #: What the build tool's environment adds: for Gradle, its JDK, the SDK and the device the tests run on.
    env: dict[str, str] = field(default_factory=dict)
    #: How many times a test run's failed tests are run again.
    retries: int = 0
    #: The tests the run is limited to, and those it leaves out, as the call named them.
    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    #: Which source this run built -- its folder, branch and commit -- as the run started (`source`).
    source: str = ""
    #: The commit that source line names, in full, and whether files differed from it: what a stamp says (`stamp`).
    commit: str | None = None
    dirty: bool = False
    process: Any = None
    state: str = "running"
    answer: str = ""
    #: What the build made to install: an app bundle, or an APK.
    app: Path | None = None
    #: The installed app's id: its bundle id, or its package name.
    bundle_id: str | None = None
    task: asyncio.Task[None] | None = None
    #: What builds it.
    toolchain: Toolchain | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        """What a person reads at the head of the answer. The plan is named only when there is one to name, so the
        usual line is no longer for having the feature."""
        plan = f" · {self.test_plan}" if self.test_plan else ""
        device = f" · on {self.device}" if self.device else ""
        whose = ", its scheme's" if self.configuration_from_scheme else ""
        return f"{self.scheme} ({self.configuration}{whose}){plan}{device}"


#: Run after a build that succeeded, with the build; answers with the lines to add (install, launch).
After = Callable[[Build], Awaitable[list[str]]]


@dataclass(frozen=True)
class Place:
    """Where a run is kept: its id, when it started, and the results path and log it writes, side by side."""

    build_id: str
    base: Path
    started: float


class Toolchain(Protocol):
    """What builds a run for one kind of device."""

    #: How the answer names what builds: ``xcodebuild``, ``Gradle``.
    name: str

    async def prepare(self, scope: Scope, request: Request, place: Place, state: StateStore) -> Build:
        """The run a call asks for, before anything is started -- or `BuildRefused` saying why it is not one."""
        ...

    async def launch(self, build: Build, request: Request) -> Any:
        """Start the run in its own process group, its output appended to its log: a process with ``pid``,
        ``returncode`` and ``wait()``. Raises `OSError` when it cannot be started."""
        ...

    async def results(self, build: Build) -> list[str]:
        """What a run that ended says, read from what it wrote; sets the build's state, and a build's app."""
        ...

    async def stamped(self, build: Build) -> tuple[Path, str]:
        """The app to install for a build that succeeded, with the commit it was built from in it where it can be,
        and the line its answer says that with."""
        ...


def _closing(build: Build) -> list[str]:
    """The lines every finished run's answer ends with: the source it built, and where its log is."""
    return [*([build.source] if build.source else []), f"log {build.log}"]


def _head_log(build: Build) -> None:
    """Start a run's log with the source it builds, before the build tool appends its own output."""
    if build.source:
        with contextlib.suppress(OSError), open(build.log, "a", encoding="utf-8") as log:
            log.write(f"{build.source}\n")


class Runner:
    """Every scope's builds and test runs, for one process, each built by the toolchain of its device's kind."""

    def __init__(
        self,
        state: StateStore,
        toolchains: Mapping[DeviceKind, Toolchain],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        signal_group: Callable[[int, int], None] = signal_process_group,
        stop_grace_s: float = STOP_GRACE_S,
        copy: HostCopy | None = None,
        source: SourceReader = read_source,
    ) -> None:
        self._state = state
        self._toolchains = dict(toolchains)
        self._source = source
        self._copy = copy or HostCopy()
        self._clock = clock
        self._wall = wall
        self._signal = signal_group
        self._stop_grace_s = stop_grace_s
        self._builds: dict[str, Build] = {}
        self._running: dict[str, Build] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    def running(self, scope_id: str) -> Build | None:
        return self._running.get(scope_id)

    def runs(self) -> list[Build]:
        """Every build or test run still going."""
        return list(self._running.values())

    def builds_for(self, device_kind: DeviceKind) -> Toolchain | None:
        """What builds for a kind of device; None when nothing here does."""
        return self._toolchains.get(device_kind)

    def toolchain(self, device_kind: DeviceKind) -> Toolchain:
        """What builds for a kind of device -- or why nothing here does."""
        found = self.builds_for(device_kind)
        if found is None:
            raise BuildRefused(f"nothing here builds for this {device_kind} device")
        return found

    async def start(
        self,
        scope: Scope,
        *,
        kind: str,
        folder: Path,
        udid: str,
        developer_dir: str,
        configuration: object = None,
        timeout_s: float,
        scheme: object = None,
        project: object = None,
        workspace: object = None,
        only_testing: object = None,
        skip_testing: object = None,
        test_plan: object = None,
        retries: int = 0,
        warnings: bool = False,
        test_diagnostics: bool = False,
        device: str = "",
        team: str = "",
        after: After | None = None,
        expected_bundle_id: str = "",
        device_kind: DeviceKind = "simulator",
        java_home: str = "",
    ) -> Build:
        """Start a build or a test run for a scope. Refuses with a reason, never runs two at once for one scope.

        A `configuration` of None is the toolchain's own choice: for xcodebuild, the one the scheme's own action builds
        this kind of run with, else Debug. A test run with an `expected_bundle_id` is refused, before it starts, when
        its tests run in another app.
        """
        if kind not in KINDS:
            raise BuildRefused(f"a run is one of {', '.join(KINDS)}")
        if test_plan is not None and kind != "test":
            raise BuildRefused("a test plan says which tests to run; name one on a test run, not a build")
        if retries and kind != "test":
            raise BuildRefused("only a test run can be retried; a build either succeeds or it does not")
        toolchain = self.toolchain(device_kind)
        request = Request(
            kind=kind,
            folder=folder,
            udid=udid,
            developer_dir=developer_dir,
            timeout_s=timeout_s,
            configuration=configuration,
            scheme=scheme,
            project=project,
            workspace=workspace,
            only_testing=only_testing,
            skip_testing=skip_testing,
            test_plan=test_plan,
            retries=retries,
            warnings=warnings,
            test_diagnostics=test_diagnostics,
            device=device,
            team=team,
            expected_bundle_id=expected_bundle_id,
            java_home=java_home,
        )
        async with self._lock:
            current = self._running.get(scope.id)
            if current is not None:
                raise BuildRefused(
                    f"{current.kind} {current.id} is still running here; wait for it with build_id {current.id}"
                )
            build_id = f"b{next(self._ids)}"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._wall()))
            base = self._state.ensure_dir(self._state.builds_dir(scope)) / f"{stamp}-{build_id}"
            build = await toolchain.prepare(scope, request, Place(build_id, base, self._clock()), self._state)
            build.toolchain = toolchain
            built_from = await self.source_of(folder)
            build.source, build.commit, build.dirty = built_from.line, built_from.commit, built_from.dirty
            _head_log(build)
            try:
                build.process = await toolchain.launch(build, request)
            except OSError as exc:
                raise BuildRefused(f"{toolchain.name} could not be started: {exc}. {self._copy.doctor_hint}") from exc
            self._forget_finished(scope.id)
            self._builds[build_id] = build
            self._running[scope.id] = build
            build.task = asyncio.get_running_loop().create_task(self._watch(build, toolchain, timeout_s, after))
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

    async def _watch(self, build: Build, toolchain: Toolchain, timeout_s: float, after: After | None) -> None:
        try:
            try:
                await asyncio.wait_for(build.process.wait(), timeout=timeout_s)
            except (asyncio.TimeoutError, TimeoutError):
                await self._end(build)
                build.state = "timed_out"
                build.answer = "\n".join(
                    [
                        f"{build.kind} {build.id} was stopped after {round(timeout_s)}s, the longest a run may take "
                        f"here (build.timeout_minutes, {self._copy.settings})",
                        *_closing(build),
                    ]
                )
                return
            lines = await toolchain.results(build)
            if build.state == "succeeded" and build.kind == "build" and after is not None:
                try:
                    lines += await after(build)
                except BuildRefused as exc:
                    lines.append(f"not launched: {exc}")
            build.answer = "\n".join([*lines, *_closing(build)])
        except asyncio.CancelledError:
            await self._end(build)
            build.state = "cancelled"
            build.answer = "\n".join([f"{build.kind} {build.id} was cancelled", *_closing(build)])
            raise
        finally:
            self._running.pop(build.scope.id, None)

    async def source_of(self, folder: Path) -> Source:
        """The source a run in `folder` would build, as git has it now (`source`)."""
        read = await self._source(folder)
        return read if isinstance(read, Source) else Source(read)

    async def stamped(self, build: Build) -> tuple[Path, str]:
        """The app to install for a build that succeeded, stamped by the toolchain that built it (`stamp`)."""
        assert build.app is not None and build.toolchain is not None, "only a build that made an app is installed"
        return await build.toolchain.stamped(build)

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
            recent = [known.id for known in self._builds.values() if known.scope.id == scope_id][-RECENT_LISTED:]
            there = f"; recent runs here are {', '.join(recent)}" if recent else "; leave build_id out to start a run"
            raise BuildRefused(f"there is no build {build_id!r}{there}")
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
