# SPDX-License-Identifier: Apache-2.0
"""Building and testing a scope's app on the scope's device, with an answer an agent can read.

`sim_build_run` and `sim_test` (`build/provider.py`) come here. A build is

    xcrun xcodebuild (-workspace W | -project P) -scheme S -configuration C
        -destination platform=iOS Simulator,id=<udid>          (platform=iOS for a real device)
        -derivedDataPath <StateStore.derived_data(scope)>
        -resultBundlePath <StateStore.builds_dir(scope)>/<stamp>-<id>.xcresult
        build | test

with its output written to a log beside the bundle, and its answer read from the bundle (`xcresult.py`) -- never
scraped from the log, whose format is Xcode's to change. A built app's path and bundle id come from
``-showBuildSettings``, so it can be installed and launched straight after. The answer and the head of the log name
the source built: the folder, and its branch and commit (`source.py`).

**Which project**: the one the build folder holds -- a workspace before the project inside it -- or the one named,
which must be inside that folder. A folder with several asks the agent to name one; a project with several schemes
asks for a scheme.

**Which configuration**: the one the call names, else ``build.configuration``, else the one the scheme's own Run
action builds with -- its Test action for tests -- as Xcode does (`schemes.py`), else Debug.

**Which app tests run in**: xcodebuild installs a test run's host app itself, so a test run held to an expected bundle
id reads its test hosts from ``-showBuildSettings test`` first (`host_apps`), and is not started when one is another.
Only targets the run's test plan turns on count (`xctestplan.py`).

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
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.build import stamp, xcresult
from sim_mirror.build.schemes import DEFAULT_CONFIGURATION, scheme_configuration, scheme_files
from sim_mirror.build.source import Source, SourceReader, read_source
from sim_mirror.build.xctestplan import planned_targets
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.codesign import resign_adhoc
from sim_mirror.platform.identifiers import build_destination, is_device_udid
from sim_mirror.platform.process import signal_group as signal_process_group
from sim_mirror.platform.xcrun import CANNOT_RUN, XCRUN_MISSING, XcrunRunner, run_xcrun, start_xcrun
from sim_mirror.scope import Scope
from sim_mirror.seams import StateStore
from sim_mirror.validation import is_xcode_name

#: Xcode's own DerivedData, where an app built outside SimMirror usually is.
DERIVED_DATA = Path("~/Library/Developer/Xcode/DerivedData").expanduser()
KINDS = ("build", "test")
LIST_TIMEOUT_S = 60.0
SETTINGS_TIMEOUT_S = 120.0
RESULT_TIMEOUT_S = 60.0
STOP_GRACE_S = 5.0
TESTS_MAX = 50
TEST_ID_MAX = 300
#: The longest project or workspace name a call may give.
PATH_MAX = 500

#: A workspace's references to the projects it builds, relative to the folder the workspace is in.
_PROJECT_REF = re.compile(r'location\s*=\s*"(?:group|container):([^"]+\.xcodeproj)"')
#: A development team a build setting names -- ``DEVELOPMENT_TEAM = ABCDE12345;``, for any SDK -- in a project file or
#: an `.xcconfig`.
_TEAM = re.compile(r'\bDEVELOPMENT_TEAM(?:\[[^\]\n]*\])?"?[ \t]*=[ \t]*"?([A-Z0-9]{10})\b')
#: Folders a project's own sources and settings are never in, left out when looking through the project folder.
_NOT_SOURCES = frozenset({"DerivedData", "build", "node_modules"})


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


@dataclass(frozen=True)
class Listing:
    """What ``xcodebuild -list`` says a project or a workspace has. A workspace lists no configurations of its own."""

    schemes: tuple[str, ...] = ()
    configurations: tuple[str, ...] = ()


def _names(values: object) -> tuple[str, ...]:
    return tuple(value for value in values if isinstance(value, str)) if isinstance(values, list) else ()


def _named(value: object, what: str, known: Sequence[str] = ()) -> str:
    """A scheme, build configuration or test plan as a call names it -- and every name a refusal lists passes here, or
    an agent told to use one would be refused for it."""
    if not is_xcode_name(value):
        there = f"; it is one of {', '.join(known)}" if known else ""
        raise BuildRefused(
            f"{what} must be a name as Xcode shows it, like Debug or App (Staging), not {value!r}{there}"
        )
    return value


def _listed(names: Sequence[str]) -> str:
    return ", ".join(names) or "none"


def _is_test_id(value: object) -> bool:
    """A test as a failure names it (`xcresult.runnable_id`): ``ParsingTests/withArguments(value:)`` has a colon, and
    a target may have spaces. Each reaches xcodebuild inside one argument, ``-only-testing:<id>``, so only a line break
    or a stray space could make it name something else."""
    return isinstance(value, str) and 0 < len(value) <= TEST_ID_MAX and value.isprintable() and value == value.strip()


def _test_ids(values: object, what: str) -> list[str]:
    if values is None:
        return []
    if not (isinstance(values, list) and len(values) <= TESTS_MAX and all(_is_test_id(value) for value in values)):
        raise BuildRefused(
            f"{what} is a list of at most {TESTS_MAX} tests as a failure names them: a target, "
            "AppTests/LoginTests, AppTests/LoginTests/testLogin or AppTests/ParsingTests/countsTrips()"
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


def _project_files(folder: Path) -> Iterator[tuple[str, list[str]]]:
    """Each folder under ``folder`` a project's own files can be in, and its files: build output, dependencies and
    hidden folders are left out. A project's folder is small once those are."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if name not in _NOT_SOURCES and not name.startswith("."))
        yield root, sorted(files)


def _configs(folder: Path) -> list[tuple[str, int]]:
    """Every `.xcconfig` under ``folder`` -- where a bundle id or product name can be set -- and when it changed."""
    return [
        (os.path.join(root, name), _stamp(Path(root) / name))
        for root, files in _project_files(folder)
        for name in files
        if name.endswith(".xcconfig")
    ]


def source_paths(folder: Path, names: Collection[str]) -> dict[str, str]:
    """Where each bare file name a test failure gives is in the project, relative to its folder.

    A failure on Xcode 26.6 says only ``TripTests.swift:6``, which an agent would then have to go and find (27.0 gives
    the whole path). A name only one file in the project has is that file; a name several have is left as it came, since
    picking one could send the agent to the wrong file.
    """
    found: dict[str, list[str]] = {name: [] for name in names if name and "/" not in name}
    for root, files in _project_files(folder) if found else ():
        for name in found.keys() & set(files):
            found[name].append(os.path.relpath(os.path.join(root, name), folder))
    return {name: paths[0] for name, paths in found.items() if len(paths) == 1}


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
    for named in _members(project):
        stamps += [(str(named), _stamp(named / "project.pbxproj")), *_schemes(named)]
    return (*stamps, *_configs(project.path.parent))


def _members(project: Project) -> list[Path]:
    """The projects a workspace names; none for a project."""
    if project.path.suffix != ".xcworkspace":
        return []
    try:
        contents = (project.path / "contents.xcworkspacedata").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [project.path.parent / location for location in _PROJECT_REF.findall(contents)]


def project_team(project: Project) -> str | None:
    """The development team a project signs its builds with, as its own settings name it: the one named most across
    its project files -- a workspace's too -- and the `.xcconfig` files beside it. None when none is named."""
    files = [
        *(folder / "project.pbxproj" for folder in (project.path, *_members(project))),
        *(Path(path) for path, _ in _configs(project.path.parent)),
    ]
    named: Counter[str] = Counter()
    for path in files:
        try:
            named.update(_TEAM.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return named.most_common(1)[0][0] if named else None


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
            there = _listed([found.name for found in projects(folder)])
            raise BuildRefused(f"{named} is not an Xcode {flag[1:]} in {folder}; it has {there}")
        if not path.is_relative_to(folder.resolve()):
            raise BuildRefused(f"the {flag[1:]} must be inside {folder}")
        return Project(flag, path)
    everything = projects(folder)
    workspaces = [path for path in everything if path.suffix == ".xcworkspace"]
    found = workspaces or everything
    if len(found) == 1:
        return Project("-workspace" if workspaces else "-project", found[0])
    if not found:
        raise BuildRefused(
            f"there is no Xcode project or workspace in {folder}, the folder builds run in; "
            "name one inside it with project or workspace"
        )
    raise BuildRefused(f"this folder has several; name one: {_listed([path.name for path in found])}")


def projects(folder: Path) -> list[Path]:
    """The workspaces, then the projects, at the top of a folder."""
    return [
        path for suffix in (".xcworkspace", ".xcodeproj") for path in sorted(folder.glob(f"*{suffix}")) if path.is_dir()
    ]


def _scheme(target: Project, listing: Listing, scheme: object) -> str:
    """The scheme named, or the only one there is."""
    schemes = listing.schemes
    if scheme is not None:
        name = _named(scheme, "scheme", schemes)
        if name not in schemes:
            raise BuildRefused(f"{target.path.name} has no scheme {name}; it has {_listed(schemes)}")
        return name
    if len(schemes) == 1:
        return schemes[0]
    if not schemes:
        raise BuildRefused(
            f"{target.path.name} has no schemes to build: make one in Xcode (Product > Scheme > New Scheme) and tick "
            "Shared under Manage Schemes, so it is kept with the project"
        )
    raise BuildRefused(f"{target.path.name} has several schemes; name one: {_listed(schemes)}")


def _configuration(target: Project, listing: Listing, configuration: object, scheme: str = "") -> str:
    """The build configuration named -- by the call or the settings, or by the `scheme` it was read from -- when the
    project has it. A workspace's are its projects', which it does not list, so there the name is xcodebuild's to
    find."""
    known = listing.configurations
    name = _named(configuration, "configuration", known)
    if known and name not in known:
        whose = f", which the scheme {scheme} builds with" if scheme else ""
        raise BuildRefused(
            f"{target.path.name} has no build configuration {name}{whose}; it has {_listed(known)} "
            "(name one with configuration, or change build.configuration)"
        )
    return name


def _targets(settings: object) -> list[tuple[str, dict[str, Any]]]:
    """Each target ``-showBuildSettings -json`` names, with its build settings."""
    return [
        (str(entry.get("target") or ""), entry["buildSettings"])
        for entry in (settings if isinstance(settings, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("buildSettings"), dict)
    ]


def _wraps(values: dict[str, Any], extension: str) -> bool:
    wrapper = values.get("WRAPPER_NAME")
    return isinstance(wrapper, str) and wrapper.endswith(extension)


def _host(values: dict[str, Any], apps: Sequence[tuple[str, dict[str, Any]]]) -> tuple[str, str] | None:
    """The app target a test bundle runs in, and its bundle id: the app its ``TEST_HOST`` is inside -- a unit test's,
    ``…/App.app//App`` as Xcode resolves it -- or the one its ``TEST_TARGET_NAME`` names, a UI test's. None for a
    bundle with neither, which runs in no app of the project's, or for a host the settings do not list."""
    test_host, test_target = values.get("TEST_HOST"), values.get("TEST_TARGET_NAME")
    for target, app in apps:
        path = os.path.normpath(os.path.join(str(app.get("TARGET_BUILD_DIR") or ""), app["WRAPPER_NAME"]))
        hosted = isinstance(test_host, str) and os.path.normpath(test_host).startswith(path + os.sep)
        if hosted or (test_target and test_target == target):
            bundle_id = app.get("PRODUCT_BUNDLE_IDENTIFIER")
            return (target, bundle_id) if isinstance(bundle_id, str) and bundle_id else None
    return None


def host_apps(settings: object) -> list[tuple[str, str, str]]:
    """Each test bundle ``-showBuildSettings test`` names that runs in an app of the project -- the test target, the
    app target and that app's bundle id -- which xcodebuild installs on the device to run it. A logic test bundle,
    hosted by no app, installs nothing and is not listed."""
    targets = _targets(settings)
    apps = [(target, values) for target, values in targets if _wraps(values, ".app")]
    hosts = []
    for target, values in targets:
        host = _host(values, apps) if _wraps(values, ".xctest") else None
        if host is not None:
            hosts.append((target, *host))
    return hosts


def _runs(target: str, only: Sequence[str], skip: Sequence[str]) -> bool:
    """Whether a run with these ``-only-testing`` and ``-skip-testing`` ids runs any of a test target's tests: each id
    begins with its target's name, and a target's alone names all of them."""
    return (not only or any(test.split("/")[0] == target for test in only)) and target not in skip


def _planned(target: str, planned: frozenset[str] | None, only: Sequence[str]) -> bool:
    """Whether a run's test plan runs a test target: when the plan is known, a target it turns on -- or one
    ``-only-testing`` names, which is checked all the same, as whether xcodebuild runs it then is Xcode's to say."""
    return planned is None or target in planned or bool(only)


def _scheme_configuration(target: Project, scheme: str, kind: str) -> str | None:
    """The configuration a scheme's own action builds this kind of run with, when its file says (`schemes.py`)."""
    return scheme_configuration(scheme_files([target.path, *_members(target)], scheme), kind)


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
    #: Whether the configuration is the one the scheme's own action builds with, as no call or setting named one.
    configuration_from_scheme: bool = False
    #: The scheme's test plan this run names, or "" when the scheme has none and Xcode uses its own test action.
    test_plan: str = ""
    #: Every scheme the project has, for a refusal that has to point at another.
    schemes: tuple[str, ...] = ()
    #: Whether the answer lists warnings as well as errors.
    warnings: bool = False
    #: The simulator a test run was sent to, when it is not the scope's own device; "" when it is.
    device: str = ""
    #: What signs a build for a real device: leave to register it and make its profile, and a team when the project
    #: names none (`signing`).
    signing: tuple[str, ...] = ()
    #: Which source this run built -- its folder, branch and commit -- as the run started (`source`).
    source: str = ""
    #: The commit that source line names, in full, and whether files differed from it: what a stamp says (`stamp`).
    commit: str | None = None
    dirty: bool = False
    process: Any = None
    state: str = "running"
    answer: str = ""
    app: Path | None = None
    bundle_id: str | None = None
    task: asyncio.Task[None] | None = None

    @property
    def label(self) -> str:
        """What a person reads at the head of the answer. The plan is named only when there is one to name, so the
        usual line is no longer for having the feature."""
        plan = f" · {self.test_plan}" if self.test_plan else ""
        device = f" · on {self.device}" if self.device else ""
        whose = ", its scheme's" if self.configuration_from_scheme else ""
        return f"{self.scheme} ({self.configuration}{whose}){plan}{device}"


def _settings_key(build: Build) -> tuple[str, ...]:
    """What a run's build settings are kept under while its project is unchanged. A real device's app is built into
    another folder than a simulator's: each is its own."""
    platform = "iOS" if is_device_udid(build.udid) else "iOS Simulator"
    return (
        str(build.project.path),
        build.developer_dir,
        build.scheme,
        build.configuration,
        str(build.derived),
        platform,
    )


#: Run after a build that succeeded, with the build; answers with the lines to add (install, launch).
After = Callable[[Build], Awaitable[list[str]]]


def _closing(build: Build) -> list[str]:
    """The lines every finished run's answer ends with: the source it built, and where its log is."""
    return [*([build.source] if build.source else []), f"log {build.log}"]


def _head_log(build: Build) -> None:
    """Start a run's log with the source it builds, before xcodebuild appends its own output."""
    if build.source:
        with contextlib.suppress(OSError), open(build.log, "a", encoding="utf-8") as log:
            log.write(f"{build.source}\n")


#: How many finished runs a scope keeps readable by build_id; older ones are let go.
KEEP_FINISHED = 20
#: How many of a scope's runs a refusal for an unknown build_id names.
RECENT_LISTED = 5


def signing(udid: str, team: str, own_team: str | None = None) -> tuple[str, ...]:
    """What xcodebuild is told to sign a build for this device with: nothing for a simulator, which needs no signing.
    For a real device, Xcode may register it and make the profile it needs through the account signed in to it, as
    Xcode does when you run on a device; the project's own team signs it, and `team` only a project that names none --
    so several projects, each with its team, build for one device side by side."""
    if not is_device_udid(udid):
        return ()
    return ("-allowProvisioningUpdates", *(() if own_team or not team else (f"DEVELOPMENT_TEAM={team}",)))


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
        copy: HostCopy | None = None,
        source: SourceReader = read_source,
    ) -> None:
        self._state = state
        self._source = source
        self._copy = copy or HostCopy()
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
        self._listings: dict[tuple[str, str], tuple[tuple[Any, ...], Listing]] = {}
        #: A scheme's test plans, keyed by project, scheme and Xcode, and thrown away when the project changes.
        self._plans: dict[tuple[str, str, str], tuple[tuple[Any, ...], list[str]]] = {}
        self._apps: dict[tuple[str, ...], tuple[tuple[Any, ...], Path, str | None]] = {}
        #: The apps a scheme's tests run in (`host_apps`), kept the same way as a built app.
        self._hosts: dict[tuple[str, ...], tuple[tuple[Any, ...], list[tuple[str, str, str]]]] = {}
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
    ) -> Build:
        """Start a build or a test run for a scope. Refuses with a reason, never runs two at once for one scope.

        A `configuration` of None is the one the scheme's own action builds this kind of run with, else Debug. A test
        run with an `expected_bundle_id` is refused, before xcodebuild starts, when its tests run in another app.
        """
        if kind not in KINDS:
            raise BuildRefused(f"a run is one of {', '.join(KINDS)}")
        if test_plan is not None and kind != "test":
            raise BuildRefused("a test plan says which tests to run; name one on a test run, not a build")
        if retries and kind != "test":
            raise BuildRefused("only a test run can be retried; a build either succeeds or it does not")
        async with self._lock:
            current = self._running.get(scope.id)
            if current is not None:
                raise BuildRefused(
                    f"{current.kind} {current.id} is still running here; wait for it with build_id {current.id}"
                )
            target = find_project(folder, project=project, workspace=workspace)
            only, skip = _test_ids(only_testing, "only_testing"), _test_ids(skip_testing, "skip_testing")
            listing = await self._listing(target, developer_dir, folder)
            chosen = _scheme(target, listing, scheme)
            from_scheme = _scheme_configuration(target, chosen, kind) if configuration is None else None
            chosen_configuration = _configuration(
                target,
                listing,
                configuration if configuration is not None else from_scheme or DEFAULT_CONFIGURATION,
                chosen if from_scheme else "",
            )
            plan = await self._test_plan(target, chosen, test_plan, developer_dir, folder)
            build_id = f"b{next(self._ids)}"
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._wall()))
            bundle = self._state.ensure_dir(self._state.builds_dir(scope)) / f"{stamp}-{build_id}.xcresult"
            build = Build(
                id=build_id,
                scope=scope,
                kind=kind,
                project=target,
                scheme=chosen,
                test_plan=plan,
                configuration=chosen_configuration,
                configuration_from_scheme=from_scheme is not None,
                udid=udid,
                developer_dir=developer_dir,
                folder=folder,
                derived=self._state.derived_data(scope),
                bundle=bundle,
                log=bundle.with_suffix(".log"),
                started=self._clock(),
                schemes=listing.schemes,
                warnings=warnings,
                device=device,
                signing=signing(udid, team, project_team(target) if is_device_udid(udid) else None),
            )
            if kind == "test" and expected_bundle_id:
                await self._hold_to(build, expected_bundle_id, only, skip)
            built_from = await self.source_of(folder)
            build.source, build.commit, build.dirty = built_from.line, built_from.commit, built_from.dirty
            _head_log(build)
            argv = [
                "xcodebuild",
                *target.args,
                "-scheme",
                chosen,
                "-configuration",
                chosen_configuration,
                "-destination",
                build_destination(udid),
                "-derivedDataPath",
                str(build.derived),
                "-resultBundlePath",
                str(bundle),
                *(("-testPlan", plan) if plan else ()),
                # Left to Xcode, a test run with a failure goes on to `simctl diagnose --timeout=600` before it ends
                # (measured on Xcode 26.6), which the answer does not need: its failures are in the result bundle.
                *(
                    ("-collect-test-diagnostics", "on-failure" if test_diagnostics else "never")
                    if kind == "test"
                    else ()
                ),
                # `-test-iterations` counts the first run too, and on its own it would re-run the tests that
                # passed as well; `-retry-tests-on-failure` is what confines the repeats to the ones that failed.
                *(("-retry-tests-on-failure", "-test-iterations", str(retries + 1)) if retries else ()),
                *(f"-only-testing:{test}" for test in only),
                *(f"-skip-testing:{test}" for test in skip),
                *build.signing,
                kind,
            ]
            try:
                build.process = await self._start(*argv, log_path=build.log, developer_dir=developer_dir, cwd=folder)
            except OSError as exc:
                raise BuildRefused(f"xcodebuild could not be started: {exc}. {self._copy.doctor_hint}") from exc
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

    async def _listing(self, target: Project, developer_dir: str, folder: Path) -> Listing:
        """The project's schemes and configurations, asked for again only once the project has changed."""
        stamp = changed(target)
        known = self._listings.get((str(target.path), developer_dir))
        if known is not None and known[0] == stamp:
            return known[1]
        listed = await self._json(
            ("xcodebuild", "-list", "-json", *target.args),
            developer_dir,
            folder,
            LIST_TIMEOUT_S,
            refuse=f"xcodebuild could not list {target.path.name}",
        )
        body = (listed.get("workspace") or listed.get("project")) if isinstance(listed, dict) else None
        found = body if isinstance(body, dict) else {}
        listing = Listing(_names(found.get("schemes")), _names(found.get("configurations")))
        self._listings[(str(target.path), developer_dir)] = (stamp, listing)
        return listing

    async def _test_plan(self, target: Project, scheme: str, wanted: object, developer_dir: str, folder: Path) -> str:
        """The test plan to run, or "" for a scheme that has none.

        A scheme either has test plans or has the older test action, and `xcodebuild -showTestPlans` answers
        ``{"testPlans": null}`` for the second (measured on Xcode 26.6; both answers are fixtures). Naming a plan a
        scheme does not have is refused with the ones it does, because an agent cannot see the scheme and guessing
        again is the only other thing it can do. Naming none leaves the choice to Xcode, which runs the scheme's
        default -- the same thing that happened before this argument existed.
        """
        if wanted is None:
            return ""
        stamp = changed(target)
        known = self._plans.get((str(target.path), scheme, developer_dir))
        if known is not None and known[0] == stamp:
            plans = known[1]
        else:
            listed = await self._json(
                ("xcodebuild", "-showTestPlans", "-scheme", scheme, "-json", *target.args),
                developer_dir,
                folder,
                LIST_TIMEOUT_S,
                refuse=f"xcodebuild could not list the test plans of {scheme}",
            )
            entries = listed.get("testPlans") if isinstance(listed, dict) else None
            plans = [str(entry["name"]) for entry in entries or [] if isinstance(entry, dict) and entry.get("name")]
            self._plans[(str(target.path), scheme, developer_dir)] = (stamp, plans)
        name = _named(wanted, "test_plan", plans)
        if name in plans:
            return name
        if not plans:
            raise BuildRefused(
                f"{scheme} has no test plans; it runs the tests its scheme names, so leave test_plan out"
            )
        raise BuildRefused(f"{scheme} has no test plan {name}; it has {', '.join(plans)}")

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
            hint = f" {self._copy.doctor_hint}" if result.rc in (XCRUN_MISSING, CANNOT_RUN) else ""
            raise BuildRefused(f"{refuse}: {result.message}{hint}")
        return document

    async def _watch(self, build: Build, timeout_s: float, after: After | None) -> None:
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
            lines = await self._results(build)
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

    async def _results(self, build: Build) -> list[str]:
        bundle = str(build.bundle)
        if build.kind == "build":
            document = await self._build_results(build)
            if not isinstance(document, dict):
                build.state = "failed"
                return [f"build {build.id} ended without a result to read ({build.label}); the log says why"]
            summary = xcresult.build_summary(document)
            build.state = "succeeded" if summary.succeeded else "failed"
            if summary.succeeded:
                build.app, build.bundle_id = await self._built_app(build)
            return xcresult.render_build(summary, label=build.label, root=build.folder, warnings=build.warnings)
        report = await self._json(
            ("xcresulttool", "get", "test-results", "summary", "--path", bundle),
            build.developer_dir,
            None,
            RESULT_TIMEOUT_S,
        )
        if not isinstance(report, dict):
            build.state = "failed"
            return [f"test {build.id} ended without a result to read ({build.label}); the log says why"]
        if not xcresult.any_test_ran(report):
            document = await self._build_results(build)
            compiled = xcresult.build_summary(document) if isinstance(document, dict) else None
            if compiled is not None and not compiled.succeeded:
                build.state = "failed"
                return xcresult.render_build(
                    compiled, label=build.label, root=build.folder, warnings=build.warnings, kind="test"
                )
        tests = await self._json(
            ("xcresulttool", "get", "test-results", "tests", "--path", bundle),
            build.developer_dir,
            None,
            RESULT_TIMEOUT_S,
        )
        summary_of_tests = xcresult.suite_summary(report, tests if isinstance(tests, dict) else None)
        places = source_paths(build.folder, [failure.file for failure in summary_of_tests.failures if failure.file])
        summary_of_tests = xcresult.placed(summary_of_tests, places)
        build.state = "succeeded" if summary_of_tests.passed else "failed"
        return xcresult.render_tests(summary_of_tests, label=build.label, root=build.folder)

    async def _build_results(self, build: Build) -> Any:
        return await self._json(
            ("xcresulttool", "get", "build-results", "--path", str(build.bundle)),
            build.developer_dir,
            None,
            RESULT_TIMEOUT_S,
        )

    async def _built_app(self, build: Build) -> tuple[Path | None, str | None]:
        """Where the built app is and what it is called, from the build settings of its application target.

        Kept for the next build of the same scheme and configuration while the project is unchanged and the app is
        still where they said.
        """
        key = _settings_key(build)
        stamp = changed(build.project)
        known = self._apps.get(key)
        if known is not None and known[0] == stamp and known[1].exists():
            return known[1], known[2]
        app, bundle_id = await self._read_built_app(build)
        if app is not None:
            self._apps[key] = (stamp, app, bundle_id)
        return app, bundle_id

    async def _read_built_app(self, build: Build) -> tuple[Path | None, str | None]:
        for _, values in _targets(await self._settings(build)):
            if _wraps(values, ".app") and values.get("TARGET_BUILD_DIR"):
                app = Path(values["TARGET_BUILD_DIR"]) / values["WRAPPER_NAME"]
                return app, values.get("PRODUCT_BUNDLE_IDENTIFIER")
        return None, None

    async def _settings(self, build: Build, *action: str, refuse: str | None = None) -> Any:
        """A run's build settings for its scheme, configuration and destination: its build action's, or another
        action's -- ``test`` lists the test bundles too, which a build's leaves out (measured on Xcode 26)."""
        return await self._json(
            (
                "xcodebuild",
                "-showBuildSettings",
                *action,
                "-json",
                *build.project.args,
                "-scheme",
                build.scheme,
                "-configuration",
                build.configuration,
                "-destination",
                build_destination(build.udid),
                "-derivedDataPath",
                str(build.derived),
            ),
            build.developer_dir,
            build.folder,
            SETTINGS_TIMEOUT_S,
            refuse=refuse,
        )

    async def _hold_to(self, build: Build, expected: str, only: Sequence[str], skip: Sequence[str]) -> None:
        """Refuse a test run whose tests run in another app than `expected`. xcodebuild installs a test host itself,
        so this is the last point before a second app -- with its own data and keychain -- is on the device. Read
        again only once the project has changed; settings that cannot be read refuse the run, as nothing else would
        stop it. A target the run's test plan turns off is not checked; its plan is read each time, as a plan's
        changes are not the project's."""
        key, stamp = _settings_key(build), changed(build.project)
        known = self._hosts.get(key)
        if known is not None and known[0] == stamp:
            hosts = known[1]
        else:
            refuse = f"xcodebuild could not say which app {build.scheme}'s tests run in, for build.expected_bundle_id"
            hosts = host_apps(await self._settings(build, "test", refuse=refuse))
            self._hosts[key] = (stamp, hosts)
        places = scheme_files([build.project.path, *_members(build.project)], build.scheme)
        planned = planned_targets(places, build.test_plan)
        for test, host, bundle_id in hosts:
            if bundle_id != expected and _runs(test, only, skip) and _planned(test, planned, only):
                raise BuildRefused(
                    f"{build.scheme} ({build.configuration}) runs {test} in {host}, which is {bundle_id}, not "
                    f"{expected}, the app build.expected_bundle_id names, so the tests were not run and nothing was "
                    f"installed: name the configuration that builds {expected} with configuration, or change "
                    "build.expected_bundle_id"
                )

    async def source_of(self, folder: Path) -> Source:
        """The source a run in `folder` would build, as git has it now (`source`)."""
        read = await self._source(folder)
        return read if isinstance(read, Source) else Source(read)

    async def stamped(self, build: Build) -> tuple[Path, str]:
        """The app to install for a build that succeeded, with the commit it was built from stamped in (`stamp`), and
        the line its answer says that with -- or the app as built, and why it carries no stamp."""
        assert build.app is not None, "only a build that made an app is installed"
        if is_device_udid(build.udid):
            return build.app, "not stamped: a real device's app is signed for it, and SimMirror does not sign it again"
        if build.commit is None:
            return build.app, f"not stamped: git named no commit for {build.folder}"

        async def resign(app: Path) -> str | None:
            return await resign_adhoc(app, developer_dir=build.developer_dir, xcrun=self._xcrun)

        mark = stamp.Stamp(build.commit, build.dirty)
        try:
            copy = await stamp.stamp_app(build.app, mark, resign)
        except stamp.StampRefused as exc:
            return build.app, f"not stamped: {exc}"
        return copy, f"stamped {stamp.described(mark)} into its Info.plist ({stamp.SHA_KEY})"

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
