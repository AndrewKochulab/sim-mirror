# SPDX-License-Identifier: Apache-2.0
"""Building and testing an Android app with its project's own Gradle wrapper, for an Android emulator or phone.

`sim_build_run` and `sim_test` (`build/provider.py`) come here for an Android device (`runner.Runner` picks the
toolchain by the device's kind). A build is

    ./gradlew --console=plain :<module>:assemble<Variant>                       in the project folder
    ./gradlew --console=plain :<module>:connected<Variant>AndroidTest            for tests, on the device

run with ``JAVA_HOME`` (`platform.java`), ``ANDROID_HOME`` (the device's SDK) and, for tests, ``ANDROID_SERIAL`` set,
its output written to a log beside the run's results folder. Measured with Gradle 9.3 and AGP 9.1:

* The APK built, and the package it installs as, are in the module's ``build/outputs/apk/…/output-metadata.json``;
  SimMirror installs and launches it as it does any app (`DeviceControl`).
* Tests write JUnit XML (`junit.py`), copied into the run's results folder after each go, so a retry's results do not
  replace the first's. A compile that fails is read from the log (`gradle_log.py`).
* Tests run with ``leaveApksInstalledAfterRun``: left to itself, AGP uninstalls the app after its tests, and with it
  everything the app had on the device.

**What is built**: ``scheme`` names the application module -- ``app``, or ``:feature:app`` -- else the one module that
applies the Android application plugin; ``configuration`` the build variant, else ``build.configuration``, else
``debug``, matched as a person would type it (``Debug`` is ``debug``). Xcode's ``project``, ``workspace`` and
``test_plan`` mean nothing here and are refused. Only a project with its own wrapper (``gradlew``) is built: never a
Gradle the Mac happens to have, which would be another build than the project's.

**A dropped connection**: adb drops an emulator's connection now and then under load and makes it again in a second
or two; AGP then fails the run with ``device offline``. A test run that failed so before any test ran is run once more,
once the device is reachable again.

**Which tests**: ``only_testing`` and ``skip_testing`` name a class -- ``dev.example.MainActivityTest`` -- or one test
of it -- ``dev.example.MainActivityTest/addsAnItem`` -- as a failure names it, and reach the instrumentation runner as
its ``class`` and ``notClass`` arguments. ``retries`` runs the tests that failed again, up to that many times.

**Which app**: with ``build.expected_bundle_id`` set, a test run builds the app first and stops before any test, and
before anything is installed, when its package is another; the test package AGP installs beside it, ``<id>.test``, is
the tests themselves.

A run cancelled or timed out ends the Gradle client with its process group; the Gradle daemon it talked to drops the
build, and stays for the next one until it idles out, as it does after any build.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.build import gradle_log, junit, stamp
from sim_mirror.build.project_files import ending_paths, project_files
from sim_mirror.build.runner import Build, BuildRefused, Place, Project, Request, test_ids
from sim_mirror.build.xcresult import SuiteSummary, placed, render_build, render_tests
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import PHONE_PREFIX
from sim_mirror.platform.java import Jdk, find_jdk
from sim_mirror.platform.process import CANNOT_RUN, spawn
from sim_mirror.scope import Scope
from sim_mirror.seams import StateStore

SETTINGS_FILES = ("settings.gradle.kts", "settings.gradle")
BUILD_FILES = ("build.gradle.kts", "build.gradle")
WRAPPER = "gradlew"
DEFAULT_VARIANT = "debug"
#: How deep under the project folder an application module is looked for.
MODULE_DEPTH = 3
#: Keeps the app -- and everything it holds on the device -- installed after its tests.
LEAVE_INSTALLED = "-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true"
#: An instrumentation runner argument, given to Gradle as a project property.
RUNNER_ARGUMENT = "-Pandroid.testInstrumentationRunnerArguments.{}={}"
#: Where a module's test results are, and the files Gradle writes there.
TEST_RESULTS = Path("build/outputs/androidTest-results/connected")
RESULT_FILES = "TEST-*.xml"
APK_OUTPUTS = Path("build/outputs/apk")
#: The folder in a run's results each go of its tests is kept in: ``attempt-1``, ``attempt-2``…
ATTEMPT = "attempt-{}"
#: An app module's build file applies the application plugin: by id, or through a version catalog's alias.
_APPLICATION = re.compile(r"""com\.android\.application|plugins\.android\.application""")
_MODULE = re.compile(r"\A:?[A-Za-z0-9_-]{1,100}(?::[A-Za-z0-9_-]{1,100}){0,5}\Z")
_VARIANT = re.compile(r"\A[A-Za-z][A-Za-z0-9]{0,63}\Z")
#: A test as a failure names it: a class, and maybe one of its tests.
_TEST = re.compile(r"\A[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:/[A-Za-z_$][\w$]*)?\Z")
_PACKAGE = re.compile(r"\A[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+\Z")
_TEST_EXAMPLE = "com.example.MainActivityTest or com.example.MainActivityTest/addsAnItem"
#: What AGP's log says when adb dropped the device's connection under it.
DEVICE_OFFLINE = "device offline"
#: How long a device whose connection dropped is waited for, and how often it is asked after.
BACK_WITHIN_S = 15.0
BACK_EVERY_S = 0.5

SerialOf = Callable[[str], Awaitable[str]]


class GradleStarter(Protocol):
    """How a Gradle wrapper call is started: `start_gradle`, or a fake."""

    async def __call__(self, args: Sequence[str], *, folder: Path, log_path: Path, env: Mapping[str, str]) -> Any:
        """Start ``./gradlew <args>`` in `folder`, in its own process group, its output appended to `log_path`."""
        ...


async def start_gradle(args: Sequence[str], *, folder: Path, log_path: Path, env: Mapping[str, str]) -> Any:
    """Start a project's own Gradle wrapper -- through ``sh``, so a wrapper checked out without its executable bit
    runs too -- with `env` added to SimMirror's environment."""
    return await spawn(["/bin/sh", str(folder / WRAPPER), *args], log_path, cwd=folder, env={**os.environ, **env})


def find_build(folder: Path) -> Project:
    """The Gradle build a folder holds, by its settings file -- or why it holds none SimMirror builds."""
    settings = next((folder / name for name in SETTINGS_FILES if (folder / name).is_file()), None)
    if settings is None:
        raise BuildRefused(
            f"{folder} holds no Gradle build (settings.gradle.kts or settings.gradle) to build for this Android device"
        )
    if not (folder / WRAPPER).is_file():
        raise BuildRefused(
            f"{folder} has no Gradle wrapper ({WRAPPER}): SimMirror builds an Android project only with its own "
            "wrapper; add one with Android Studio, or the project's own Gradle"
        )
    return Project("settings", settings)


def app_modules(folder: Path) -> list[str]:
    """The modules that build an Android app, as Gradle names them: ``app``, ``feature:app``."""
    found = []
    for root, files in project_files(folder):
        place = Path(root).relative_to(folder)
        if place.parts and len(place.parts) <= MODULE_DEPTH:
            build_file = next((Path(root) / name for name in BUILD_FILES if name in files), None)
            if build_file is not None and _APPLICATION.search(_read(build_file)):
                found.append(":".join(place.parts))
    return sorted(found)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _module(value: object, modules: Sequence[str]) -> str:
    """The application module a call names, or the project's one -- or why there is none to build."""
    if value is None:
        if len(modules) == 1:
            return modules[0]
        if not modules:
            raise BuildRefused(
                "no module of this project applies the Android application plugin, so none builds an app"
            )
        raise BuildRefused(f"this project has several app modules; name one with scheme: {', '.join(modules)}")
    if not isinstance(value, str) or not _MODULE.match(value):
        raise BuildRefused("scheme names the app module to build, as Gradle does: app, or :feature:app")
    name = value.removeprefix(":")
    if name not in modules:
        known = f"; its app modules are {', '.join(modules)}" if modules else ""
        raise BuildRefused(f"{name} is not an app module of this project{known}")
    return name


def variant_of(value: object) -> str:
    """A build variant as Gradle names it, from the way a person types it: ``Debug`` and ``DEBUG`` are ``debug``,
    ``freeDebug`` is itself."""
    if value is None:
        return DEFAULT_VARIANT
    if not isinstance(value, str) or not _VARIANT.match(value):
        raise BuildRefused("configuration names a build variant, as Gradle does: debug, release, freeDebug")
    return value.lower() if value.isupper() or value[1:].islower() else value[0].lower() + value[1:]


def task(module: str, verb: str, variant: str, suffix: str = "") -> str:
    """A module's task for a variant: ``:app:assembleDebug``, ``:app:connectedDebugAndroidTest``."""
    return f":{module}:{verb}{variant[0].upper()}{variant[1:]}{suffix}"


def _test_names(values: object, what: str) -> list[str]:
    names = test_ids(values, what, _TEST_EXAMPLE)
    if not all(_TEST.match(name) for name in names):
        raise BuildRefused(f"{what} names a test class, or one test of it, as a failure names it: {_TEST_EXAMPLE}")
    return names


def runner_arguments(only: Sequence[str], skip: Sequence[str]) -> list[str]:
    """The instrumentation runner's ``class`` and ``notClass``: a test class, or ``Class#test``."""
    arguments = []
    for name, tests in (("class", only), ("notClass", skip)):
        if tests:
            arguments.append(RUNNER_ARGUMENT.format(name, ",".join(test.replace("/", "#") for test in tests)))
    return arguments


def built_app(module_folder: Path, variant: str) -> tuple[Path, str] | None:
    """The APK a variant built and the package it installs as, from the output metadata AGP writes beside it."""
    for metadata in sorted((module_folder / APK_OUTPUTS).glob("**/output-metadata.json")):
        try:
            said = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(said, dict) or said.get("variantName") != variant:
            continue
        elements = [element for element in said.get("elements") or [] if isinstance(element, dict)]
        files = [str(element["outputFile"]) for element in elements if element.get("outputFile")]
        package = said.get("applicationId")
        if files and isinstance(package, str) and _PACKAGE.match(package):
            return metadata.parent / files[0], package
    return None


def _read_kept(folder: Path) -> list[junit.Outcome]:
    return junit.read_results(sorted(folder.glob("*.xml")))


class GradleRun:
    """A Gradle run as `runner.Runner` sees a process: one or more wrapper calls in turn -- a test run's build, its
    tests, each retry -- ended together, by the process group of whichever is running.

    `steps` drives the calls after the first, with `step`, and answers the run's exit code."""

    def __init__(
        self,
        start: Callable[[Sequence[str]], Awaitable[Any]],
        first: Any,
        steps: Callable[[GradleRun, int], Awaitable[int]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start = start
        self._current = first
        self._steps = steps
        self._clock = clock
        self._started = clock()
        self._driven = False
        self.returncode: int | None = None
        self.seconds: float | None = None
        #: Why the run stopped before its tests: the app is not the one ``build.expected_bundle_id`` names.
        self.refused: str | None = None

    @property
    def pid(self) -> int:
        return int(self._current.pid)

    async def step(self, args: Sequence[str]) -> int:
        """Run one more wrapper call, and answer its exit code."""
        try:
            self._current = await self._start(args)
        except OSError:
            return CANNOT_RUN
        return int(await self._current.wait())

    async def wait(self) -> int:
        if self._driven:
            # Ended from outside: the call running now is what there is to wait for.
            return int(await self._current.wait())
        self._driven = True
        code = await self._steps(self, int(await self._current.wait()))
        self.seconds = round(self._clock() - self._started, 1)
        self.returncode = code
        return code


class GradleToolchain:
    """Gradle, building and testing for an Android emulator or phone, its answer read from what Gradle wrote."""

    name = "Gradle"

    def __init__(
        self,
        serial_of: SerialOf,
        *,
        start: GradleStarter = start_gradle,
        jdk: Callable[[str], Awaitable[Jdk | None]] = find_jdk,
        copy: HostCopy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._serial_of = serial_of
        self._start = start
        self._jdk = jdk
        self._copy = copy or HostCopy()
        self._sleep = sleep

    async def prepare(self, scope: Scope, request: Request, place: Place, state: StateStore) -> Build:
        for name, value in (("project", request.project), ("workspace", request.workspace)):
            if value is not None:
                raise BuildRefused(
                    f"{name} names an Xcode project; an Android device's build is the Gradle build in the folder, "
                    "so leave it out"
                )
        if request.test_plan is not None:
            raise BuildRefused("a Gradle build has no test plans; name its tests with only_testing instead")
        project = find_build(request.folder)
        modules = app_modules(request.folder)
        module = _module(request.scheme, modules)
        only, skip = (
            _test_names(request.only_testing, "only_testing"),
            _test_names(request.skip_testing, "skip_testing"),
        )
        if not request.developer_dir:
            raise BuildRefused(f"no Android SDK is set up here, which Gradle builds with. {self._copy.doctor_hint}")
        jdk = await self._jdk(request.java_home)
        if jdk is None:
            raise BuildRefused(
                f"no JDK was found to run Gradle with: name one with android.java_home ({self._copy.settings}), or "
                "install Android Studio, which has its own"
            )
        env = {"JAVA_HOME": str(jdk.home), "ANDROID_HOME": request.developer_dir}
        if request.kind == "test":
            try:
                env["ANDROID_SERIAL"] = await self._serial_of(request.udid)
            except DeviceControlError as exc:
                raise BuildRefused(f"the tests run only on a running device: {exc}") from exc
        return Build(
            id=place.build_id,
            scope=scope,
            kind=request.kind,
            project=project,
            scheme=module,
            configuration=variant_of(request.configuration),
            udid=request.udid,
            developer_dir=request.developer_dir,
            folder=request.folder,
            derived=request.folder / module.replace(":", "/") / "build",
            bundle=place.base,
            log=place.base.with_suffix(".log"),
            started=place.started,
            schemes=tuple(modules),
            warnings=request.warnings,
            device=request.device,
            retries=request.retries,
            only=tuple(only),
            skip=tuple(skip),
            env=env,
        )

    def _module_folder(self, build: Build) -> Path:
        return build.folder / build.scheme.replace(":", "/")

    async def launch(self, build: Build, request: Request) -> Any:
        async def start(args: Sequence[str]) -> Any:
            return await self._start(["--console=plain", *args], folder=build.folder, log_path=build.log, env=build.env)

        assemble = task(build.scheme, "assemble", build.configuration)
        if build.kind == "build":
            return GradleRun(start, await start([assemble]), self._built)
        if request.expected_bundle_id:
            expected = request.expected_bundle_id
            return GradleRun(start, await start([assemble]), lambda run, code: self._held(build, run, code, expected))
        self._clear_results(build)
        first = await start(self._test_args(build, build.only))
        return GradleRun(start, first, lambda run, code: self._tested(build, run, code))

    @staticmethod
    async def _built(run: GradleRun, code: int) -> int:
        return code

    def _test_args(self, build: Build, only: Sequence[str]) -> list[str]:
        return [
            task(build.scheme, "connected", build.configuration, "AndroidTest"),
            LEAVE_INSTALLED,
            *runner_arguments(only, build.skip),
        ]

    async def _held(self, build: Build, run: GradleRun, code: int, expected: str) -> int:
        """A test run held to an app: built first, and its tests run only when the app is `expected`."""
        if code != 0:
            return code
        app = built_app(self._module_folder(build), build.configuration)
        if app is not None and app[1] != expected:
            run.refused = (
                f"{build.label} builds {app[1]}, not {expected}, the app build.expected_bundle_id names, so the tests "
                f"were not run and nothing was installed: name the variant that builds {expected} with "
                "configuration, or change build.expected_bundle_id"
            )
            return 1
        self._clear_results(build)
        return await self._tested(build, run, await run.step(self._test_args(build, build.only)))

    async def _tested(self, build: Build, run: GradleRun, code: int) -> int:
        """Keep each go's results, and run the tests that failed again while retries are left."""
        attempt = 1
        outcomes = self._keep_results(build, attempt)
        if code != 0 and not outcomes and DEVICE_OFFLINE in self._log(build) and await self._back(build.udid):
            self._clear_results(build)
            code = await run.step(self._test_args(build, build.only))
            outcomes = self._keep_results(build, attempt)
        while code != 0 and attempt <= build.retries:
            again = junit.failed(outcomes)
            if not again:
                break
            attempt += 1
            self._clear_results(build)
            code = await run.step(self._test_args(build, again))
            outcomes = self._keep_results(build, attempt)
        return code

    async def _back(self, udid: str) -> bool:
        """Whether a device whose connection dropped is reachable again within `BACK_WITHIN_S`."""
        for _ in range(int(BACK_WITHIN_S / BACK_EVERY_S)):
            await self._sleep(BACK_EVERY_S)
            try:
                await self._serial_of(udid)
            except DeviceControlError:
                continue
            return True
        return False

    def _clear_results(self, build: Build) -> None:
        """Remove the results a run before this one left, so a go that writes none is not read as having passed."""
        for old in (self._module_folder(build) / TEST_RESULTS).glob(f"**/{RESULT_FILES}"):
            old.unlink(missing_ok=True)

    def _keep_results(self, build: Build, attempt: int) -> list[junit.Outcome]:
        """Copy what a go of the tests wrote into the run's own results, and answer what it says."""
        kept = build.bundle / ATTEMPT.format(attempt)
        kept.mkdir(parents=True, exist_ok=True)
        written = sorted((self._module_folder(build) / TEST_RESULTS).glob(f"**/{RESULT_FILES}"))
        # One file per device: numbered, as two devices' files can have one name.
        for index, path in enumerate(written):
            shutil.copyfile(path, kept / f"{index}-{path.name}")
        return _read_kept(kept)

    def _attempts(self, build: Build) -> list[list[junit.Outcome]]:
        """Each go of the run's tests that ran any, in order."""
        attempts, number = [], 1
        while (build.bundle / ATTEMPT.format(number)).is_dir():
            attempts.append(_read_kept(build.bundle / ATTEMPT.format(number)))
            number += 1
        return [attempt for attempt in attempts if attempt]

    def _log(self, build: Build) -> str:
        try:
            return build.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    async def results(self, build: Build) -> list[str]:
        run = build.process
        code, seconds = run.returncode, run.seconds
        if build.kind == "build":
            summary = gradle_log.read_log(self._log(build), build.folder, succeeded=code == 0, seconds=seconds)
            build.state = "succeeded" if summary.succeeded else "failed"
            if summary.succeeded:
                found = built_app(self._module_folder(build), build.configuration)
                build.app, build.bundle_id = found if found is not None else (None, None)
            return render_build(summary, label=build.label, root=build.folder, warnings=build.warnings)
        build.state = "failed"
        if run.refused is not None:
            return [f"test FAILED · {run.refused}"]
        attempts = self._attempts(build)
        if not attempts:
            if code == 0:
                build.state = "succeeded"
                return render_tests(SuiteSummary(True, 0, 0, 0, (), seconds), label=build.label, root=build.folder)
            summary = gradle_log.read_log(self._log(build), build.folder, succeeded=False, seconds=seconds)
            return render_build(summary, label=build.label, root=build.folder, warnings=build.warnings, kind="test")
        tests = junit.summary(attempts, seconds)
        tests = placed(tests, ending_paths(build.folder, [failure.file for failure in tests.failures if failure.file]))
        lines = render_tests(tests, label=build.label, root=build.folder)
        if tests.passed and code != 0:
            # Every test passed, and Gradle failed all the same: after the tests, or for a reason of its own.
            summary = gradle_log.read_log(self._log(build), build.folder, succeeded=False, seconds=seconds)
            lines += [f"error {error.message}" for error in summary.errors]
            return lines
        build.state = "succeeded" if tests.passed else "failed"
        return lines

    async def stamped(self, build: Build) -> tuple[Path, str]:
        """The APK to install, in a folder with the commit it was built from (`stamp.stamp_apk`) -- or the APK as
        built, and why it carries no stamp."""
        assert build.app is not None and build.bundle_id is not None, "only a build that made an app is installed"
        if build.udid.startswith(f"{PHONE_PREFIX}-"):
            return build.app, "not stamped: SimMirror writes nothing to an Android phone but the app it installs"
        if build.commit is None:
            return build.app, f"not stamped: git named no commit for {build.folder}"
        mark = stamp.Stamp(build.commit, build.dirty)
        try:
            folder = stamp.stamp_apk(build.app, mark, build.bundle_id)
        except stamp.StampRefused as exc:
            return build.app, f"not stamped: {exc}"
        return folder, f"stamped {stamp.described(mark)}, kept on the device beside the app (the APK is unchanged)"
