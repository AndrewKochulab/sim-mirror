# SPDX-License-Identifier: Apache-2.0
"""A Gradle wrapper for tests: an Android project's files, and a wrapper that writes what Gradle would -- an APK and
its output metadata, a test run's JUnit XML, a log -- and ends when the test says, or is ended.

`android_project` lays out the example project's shape (`examples/android-app`); `FakeGradle` is a
`gradle.GradleStarter`. Its outputs are the ones captured from Gradle 9.3 and AGP 9.1 (``fixtures/gradle-*``,
``fixtures/junit-android-failure.xml``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sim_mirror.testing.fakes import FakeProcess, fixture

#: The package the example app installs as.
EXAMPLE_PACKAGE = "dev.simmirror.example"
EXAMPLE_TEST = "dev.simmirror.example.MainActivityTest"


def android_project(folder: Path, modules: Sequence[str] = ("app",), *, wrapper: bool = True) -> Path:
    """An Android project in `folder`: its settings, its wrapper, and a module applying the application plugin for
    each of `modules` -- ``app``, ``feature/app`` -- beside a library that does not."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.gradle.kts").write_text('rootProject.name = "Groceries"\ninclude(":app")\n')
    # The root build file names the plugin too, to be applied by the modules: it is not a module.
    (folder / "build.gradle.kts").write_text(
        'plugins {\n    id("com.android.application") version "9.1.0" apply false\n}\n'
    )
    if wrapper:
        (folder / "gradlew").write_text("#!/bin/sh\n")
    for module in modules:
        (folder / module).mkdir(parents=True, exist_ok=True)
        (folder / module / "build.gradle.kts").write_text('plugins {\n    id("com.android.application")\n}\n')
    (folder / "library").mkdir(exist_ok=True)
    (folder / "library" / "build.gradle").write_text("plugins { id 'com.android.library' }\n")
    return folder


@dataclass
class Play:
    """What one call of the wrapper does."""

    rc: int = 0
    #: Lines appended to the log.
    log: str = ""
    #: Writes the APK a build makes, with its output metadata.
    apk: bool = False
    #: The package that metadata names.
    package: str = EXAMPLE_PACKAGE
    #: JUnit XML the tests wrote; None when the call ran no tests.
    results: str | None = None
    #: Keeps running until a test finishes it, or it is ended.
    hold: bool = False


@dataclass(frozen=True)
class GradleCall:
    args: tuple[str, ...]
    folder: Path
    log_path: Path
    env: dict[str, str]


@dataclass
class FakeGradle:
    """Plays a project's Gradle wrapper: each call does the next `Play` queued with `then`, else succeeds quietly."""

    module: str = "app"
    calls: list[GradleCall] = field(default_factory=list)
    processes: list[FakeProcess] = field(default_factory=list)
    signals: list[tuple[int, int]] = field(default_factory=list)
    start_error: OSError | None = None
    _plays: list[Play] = field(default_factory=list)

    def then(self, play: Play) -> FakeGradle:
        self._plays.append(play)
        return self

    async def __call__(
        self, args: Sequence[str], *, folder: Path, log_path: Path, env: Mapping[str, str]
    ) -> FakeProcess:
        if self.start_error is not None:
            raise self.start_error
        self.calls.append(GradleCall(tuple(args), folder, log_path, dict(env)))
        play = self._plays.pop(0) if self._plays else Play()
        module = folder / self.module
        if play.apk:
            outputs = module / "build/outputs/apk/debug"
            outputs.mkdir(parents=True, exist_ok=True)
            (outputs / "app-debug.apk").write_bytes(b"APK")
            metadata = fixture("gradle-output-metadata.json").replace(EXAMPLE_PACKAGE, play.package)
            (outputs / "output-metadata.json").write_text(metadata)
        if play.results is not None:
            results = module / "build/outputs/androidTest-results/connected/debug"
            results.mkdir(parents=True, exist_ok=True)
            (results / "TEST-Pixel_9(AVD) - 16-_app-.xml").write_text(play.results)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(play.log)
        process = FakeProcess(5000 + len(self.processes))
        self.processes.append(process)
        if not play.hold:
            process.finish(play.rc)
        return process

    def signal(self, pid: int, sig: int) -> None:
        """End a call's process group, as `process.signal_group` would."""
        self.signals.append((pid, sig))
        next(process for process in self.processes if process.pid == pid).finish(-sig)


def junit_results(*cases: tuple[str, str | None]) -> str:
    """A JUnit XML document of the example's test class: each case a test's name, and its failure text or None."""
    body = "".join(
        f'<testcase name="{name}" classname="{EXAMPLE_TEST}"/>'
        if failure is None
        else f'<testcase name="{name}" classname="{EXAMPLE_TEST}"><failure>{failure}</failure></testcase>'
        for name, failure in cases
    )
    return f'<?xml version="1.0"?><testsuites><testsuite name="{EXAMPLE_TEST}">{body}</testsuite></testsuites>'
