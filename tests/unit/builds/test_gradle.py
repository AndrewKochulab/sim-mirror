# SPDX-License-Identifier: Apache-2.0
"""Building and testing an Android app with its project's Gradle wrapper, answered as an Xcode build is."""

from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.build import gradle
from sim_mirror.build.gradle import (
    LEAVE_INSTALLED,
    GradleToolchain,
    app_modules,
    built_app,
    find_build,
    runner_arguments,
    start_gradle,
    task,
    variant_of,
)
from sim_mirror.build.runner import Build, BuildRefused, Runner
from sim_mirror.build.source import Source
from sim_mirror.build.stamp import APK_STAMP, apk_stamp
from sim_mirror.platform.adb import AdbError
from sim_mirror.platform.java import Jdk
from sim_mirror.platform.process import CANNOT_RUN
from sim_mirror.testing.fakes import MemoryStateStore, fixture
from sim_mirror.testing.gradle import EXAMPLE_PACKAGE, EXAMPLE_TEST, FakeGradle, Play, android_project, junit_results
from sim_mirror.testing.rig import scope

SCOPE = scope("topic-1", "ws")
UDID = "avd-Pixel-9-fa39dd"
PHONE = "adb-R58M12ABC-9b1e04"
SDK = "/Users/dev/Library/Android/sdk"
JDK = Jdk(Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home"), "Android Studio")
FAILED = f"once\n\tat {EXAMPLE_TEST}.addsAnItem(MainActivityTest.kt:30)"


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.folder = android_project(tmp_path / "Groceries")
        self.gradle = FakeGradle()
        self.jdk: Jdk | None = JDK
        self.asked_jdk: list[str] = []
        self.commit: str | None = "c" * 40
        #: Whether the device answers adb; and how long the toolchain waited for it.
        self.reachable = True
        #: Whether the device goes away once the toolchain waits for it.
        self.stays_away = False
        self.waited: list[float] = []
        self.toolchain = GradleToolchain(self.serial_of, start=self.gradle, jdk=self.find_jdk, sleep=self.sleep)
        self.runner = Runner(
            MemoryStateStore(tmp_path),
            {"android": self.toolchain},
            clock=lambda: 100.0,
            wall=lambda: 0.0,
            signal_group=self.gradle.signal,
            stop_grace_s=0.01,
            source=self.read_source,
        )

    async def sleep(self, seconds: float) -> None:
        self.waited.append(seconds)
        self.reachable = not self.stays_away

    async def serial_of(self, udid: str) -> str:
        if udid != UDID or not self.reachable:
            raise AdbError(f"{udid} is not running or not reachable through adb now")
        return "emulator-5554"

    async def find_jdk(self, configured: str) -> Jdk | None:
        self.asked_jdk.append(configured)
        return self.jdk

    async def read_source(self, folder: Path) -> Source:
        return Source(f"source {folder} · main @ {(self.commit or 'none')[:8]}", self.commit)

    async def begin(self, kind: str = "build", **options: Any) -> Build:
        return await self.runner.start(
            options.pop("scope", SCOPE),
            kind=kind,
            folder=options.pop("folder", self.folder),
            udid=options.pop("udid", UDID),
            developer_dir=options.pop("developer_dir", SDK),
            timeout_s=options.pop("timeout_s", 60),
            device_kind="android",
            **options,
        )

    async def answer(self, kind: str = "build", **options: Any) -> tuple[Build, list[str]]:
        build = await self.begin(kind, **options)
        said = await self.runner.result(SCOPE.id, build.id, 5)
        return build, said.splitlines()

    def args(self) -> list[tuple[str, ...]]:
        return [call.args for call in self.gradle.calls]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# -- which build -----------------------------------------------------------------------------------------------------


def test_a_gradle_build_is_found_by_its_settings_and_needs_its_own_wrapper(tmp_path: Path) -> None:
    with pytest.raises(BuildRefused, match="holds no Gradle build"):
        find_build(tmp_path)
    android_project(tmp_path, wrapper=False)
    with pytest.raises(BuildRefused, match=r"has no Gradle wrapper \(gradlew\)"):
        find_build(tmp_path)
    (tmp_path / "gradlew").touch()
    assert find_build(tmp_path).args == ("settings", str(tmp_path / "settings.gradle.kts"))


def test_app_modules_are_those_applying_the_application_plugin(tmp_path: Path) -> None:
    android_project(tmp_path, ("app", "feature/phone"))
    (tmp_path / "wear").mkdir()
    (tmp_path / "wear" / "build.gradle.kts").write_text("plugins { alias(libs.plugins.android.application) }\n")
    (tmp_path / "a/b/c/d").mkdir(parents=True)
    (tmp_path / "a/b/c/d/build.gradle").write_text("apply plugin: 'com.android.application'\n")
    # A build file that cannot be read applies nothing.
    unreadable = tmp_path / "odd" / "build.gradle.kts"
    unreadable.parent.mkdir()
    unreadable.write_text('plugins { id("com.android.application") }\n')
    unreadable.chmod(0)
    try:
        assert app_modules(tmp_path) == ["app", "feature:phone", "wear"]
    finally:
        unreadable.chmod(0o644)


async def test_the_module_is_the_one_named_or_the_projects_only_one(rig: Rig, tmp_path: Path) -> None:
    android_project(tmp_path / "two", ("app", "tv"))
    with pytest.raises(BuildRefused, match="several app modules; name one with scheme: app, tv"):
        await rig.begin(folder=tmp_path / "two")
    with pytest.raises(BuildRefused, match="phone is not an app module of this project; its app modules are app, tv"):
        await rig.begin(folder=tmp_path / "two", scheme="phone")
    with pytest.raises(BuildRefused, match="scheme names the app module to build"):
        await rig.begin(scheme="app module")
    build = await rig.begin(folder=tmp_path / "two", scheme=":tv")
    assert build.scheme == "tv" and build.schemes == ("app", "tv")
    android_project(tmp_path / "none", ())
    with pytest.raises(BuildRefused, match="no module of this project applies the Android application plugin"):
        await rig.begin(folder=tmp_path / "none", scope=scope("topic-2", "ws"))
    with pytest.raises(BuildRefused, match=r"phone is not an app module of this project$"):
        await rig.begin(folder=tmp_path / "none", scheme="phone", scope=scope("topic-2", "ws"))


def test_a_variant_is_read_as_a_person_types_it() -> None:
    assert [variant_of(name) for name in (None, "debug", "Debug", "DEBUG", "freeDebug", "FreeDebug")] == [
        "debug", "debug", "debug", "debug", "freeDebug", "freeDebug",
    ]  # fmt: skip
    for wrong in ("free-debug", "", 3, "x" * 65):
        with pytest.raises(BuildRefused, match="configuration names a build variant"):
            variant_of(wrong)
    assert task("feature:app", "connected", "freeDebug", "AndroidTest") == ":feature:app:connectedFreeDebugAndroidTest"


def test_tests_reach_the_instrumentation_runner_as_classes() -> None:
    only = [EXAMPLE_TEST, f"{EXAMPLE_TEST}/addsAnItem"]
    assert runner_arguments(only, [f"{EXAMPLE_TEST}/startsEmpty"]) == [
        f"-Pandroid.testInstrumentationRunnerArguments.class={EXAMPLE_TEST},{EXAMPLE_TEST}#addsAnItem",
        f"-Pandroid.testInstrumentationRunnerArguments.notClass={EXAMPLE_TEST}#startsEmpty",
    ]
    assert runner_arguments([], []) == []


def test_the_apk_built_is_the_one_its_variants_metadata_names(tmp_path: Path) -> None:
    outputs = tmp_path / "build/outputs/apk"
    for variant, said in (("release", fixture("gradle-output-metadata.json").replace('"debug"', '"release"')),
                          ("broken", "{"), ("list", "[]"),
                          ("nameless", json.dumps({"variantName": "staging", "applicationId": "x.y", "elements": []})),
                          ("debug", fixture("gradle-output-metadata.json"))):  # fmt: skip
        (outputs / variant).mkdir(parents=True)
        (outputs / variant / "output-metadata.json").write_text(said)
    assert built_app(tmp_path, "debug") == (outputs / "debug" / "app-debug.apk", EXAMPLE_PACKAGE)
    assert built_app(tmp_path, "release") == (outputs / "release" / "app-debug.apk", EXAMPLE_PACKAGE)
    assert built_app(tmp_path, "staging") is None


# -- a build ---------------------------------------------------------------------------------------------------------


async def test_a_build_assembles_the_variant_with_the_jdk_and_sdk_and_reads_the_apk(rig: Rig) -> None:
    rig.gradle.then(Play(apk=True, log="BUILD SUCCESSFUL in 21s\n"))
    build, said = await rig.answer(configuration="Debug", java_home="/opt/jdk")
    assert rig.args() == [("--console=plain", ":app:assembleDebug")]
    call = rig.gradle.calls[0]
    assert call.folder == rig.folder and call.log_path == build.log and rig.asked_jdk == ["/opt/jdk"]
    assert call.env == {"JAVA_HOME": str(JDK.home), "ANDROID_HOME": SDK}
    assert said[0] == "build ok · app (debug) · 0s · 0 errors, 0 warnings" and said[-1] == f"log {build.log}"
    assert build.app == rig.folder / "app/build/outputs/apk/debug/app-debug.apk" and build.bundle_id == EXAMPLE_PACKAGE
    assert build.log.read_text().startswith(f"source {rig.folder}")


async def test_a_build_that_does_not_compile_answers_with_its_errors(rig: Rig) -> None:
    log = fixture("gradle-kotlin-error.log").replace("/Users/dev/Groceries", str(rig.folder))
    rig.gradle.then(Play(rc=1, log=log))
    build, said = await rig.answer()
    assert build.state == "failed" and build.app is None
    assert said[:2] == [
        "build FAILED · app (debug) · 0s · 1 error, 0 warnings",
        "error app/src/main/kotlin/dev/simmirror/example/MainActivity.kt:21:26 Initializer type mismatch: expected "
        "'Int', actual 'String'.",
    ]
    # A log that is gone says nothing but that Gradle failed.
    build.log.unlink()
    assert await rig.toolchain.results(build) == [
        "build FAILED · app (debug) · 0s · 1 error, 0 warnings",
        "error Gradle failed; the log says why",
    ]


async def test_a_build_that_leaves_no_apk_says_nothing_was_built_to_install(rig: Rig) -> None:
    build, said = await rig.answer()
    assert build.state == "succeeded" and build.app is None and said[0].startswith("build ok")


async def test_what_means_nothing_to_gradle_is_refused_before_it_starts(rig: Rig) -> None:
    for option, value in (("project", "App.xcodeproj"), ("workspace", "App.xcworkspace")):
        with pytest.raises(BuildRefused, match=f"{option} names an Xcode project"):
            await rig.begin(**{option: value})
    with pytest.raises(BuildRefused, match="a Gradle build has no test plans"):
        await rig.begin("test", test_plan="Smoke")
    with pytest.raises(BuildRefused, match="only_testing names a test class, or one test of it"):
        await rig.begin("test", only_testing=["dev.example.Main Test"])
    with pytest.raises(BuildRefused, match="no Android SDK is set up here"):
        await rig.begin(developer_dir="")
    rig.jdk = None
    with pytest.raises(BuildRefused, match=r"no JDK was found to run Gradle with: name one with android\.java_home"):
        await rig.begin()
    rig.jdk = JDK
    with pytest.raises(BuildRefused, match="the tests run only on a running device: avd-Gone-000000 is not running"):
        await rig.begin("test", udid="avd-Gone-000000")
    assert rig.gradle.calls == []


async def test_a_wrapper_that_cannot_start_is_refused(rig: Rig) -> None:
    rig.gradle.start_error = PermissionError("gradlew: permission denied")
    with pytest.raises(BuildRefused, match="Gradle could not be started: gradlew: permission denied"):
        await rig.begin()


async def test_a_build_cancelled_ends_the_gradle_client(rig: Rig) -> None:
    rig.gradle.then(Play(hold=True))
    build = await rig.begin()
    await asyncio.sleep(0)
    assert await rig.runner.cancel(SCOPE.id)
    assert rig.gradle.signals == [(5000, signal.SIGTERM)] and build.state == "cancelled"
    assert (await rig.runner.result(SCOPE.id, build.id, 1)).startswith(f"build {build.id} was cancelled")


# -- a test run ------------------------------------------------------------------------------------------------------


async def test_tests_run_on_the_device_and_leave_the_app_installed(rig: Rig) -> None:
    rig.gradle.then(Play(results=junit_results(("startsEmpty", None), ("addsAnItem", None))))
    build, said = await rig.answer("test", only_testing=[EXAMPLE_TEST], skip_testing=[f"{EXAMPLE_TEST}/slow"])
    assert rig.args() == [
        (
            "--console=plain",
            ":app:connectedDebugAndroidTest",
            LEAVE_INSTALLED,
            f"-Pandroid.testInstrumentationRunnerArguments.class={EXAMPLE_TEST}",
            f"-Pandroid.testInstrumentationRunnerArguments.notClass={EXAMPLE_TEST}#slow",
        )
    ]
    assert rig.gradle.calls[0].env["ANDROID_SERIAL"] == "emulator-5554"
    assert build.state == "succeeded" and said[0] == "test ok · app (debug) · 2 passed, 0 failed, 0 skipped · 0s"


async def test_a_failing_test_is_placed_in_the_project_and_retried(rig: Rig) -> None:
    test_file = rig.folder / "app/src/androidTest/kotlin/MainActivityTest.kt"
    test_file.parent.mkdir(parents=True)
    test_file.touch()
    failing = junit_results(("startsEmpty", None), ("addsAnItem", FAILED))
    rig.gradle.then(Play(rc=1, results=failing)).then(Play(rc=1, results=junit_results(("addsAnItem", FAILED))))
    build, said = await rig.answer("test", retries=1)
    assert rig.args()[1][-1] == f"-Pandroid.testInstrumentationRunnerArguments.class={EXAMPLE_TEST}#addsAnItem"
    assert build.state == "failed" and said[:2] == [
        "test FAILED · app (debug) · 1 passed, 1 failed, 0 skipped · 0s",
        f"fail {EXAMPLE_TEST}/addsAnItem app/src/androidTest/kotlin/MainActivityTest.kt:30 once (failed 2 times)",
    ]
    assert sorted(path.name for path in build.bundle.iterdir()) == ["attempt-1", "attempt-2"]


async def test_a_test_that_passes_on_a_retry_is_flaky(rig: Rig) -> None:
    failing = junit_results(("startsEmpty", None), ("addsAnItem", FAILED))
    rig.gradle.then(Play(rc=1, results=failing)).then(Play(results=junit_results(("addsAnItem", None))))
    build, said = await rig.answer("test", retries=2)
    assert len(rig.gradle.calls) == 2 and build.state == "succeeded"
    assert said[:2] == [
        "test ok · app (debug) · 2 passed, 0 failed, 0 skipped · 0s",
        f"flaky {EXAMPLE_TEST}/addsAnItem failed, then passed on attempt 2",
    ]


async def test_a_failed_go_with_no_failed_tests_is_not_retried(rig: Rig) -> None:
    rig.gradle.then(Play(rc=1, results=junit_results(("startsEmpty", None)), log="* What went wrong:\nNo space\n"))
    build, said = await rig.answer("test", retries=2)
    assert len(rig.gradle.calls) == 1 and build.state == "failed"
    assert said[:2] == ["test ok · app (debug) · 1 passed, 0 failed, 0 skipped · 0s", "error No space"]


async def test_tests_that_did_not_build_answer_with_the_compile_errors(rig: Rig) -> None:
    rig.gradle.then(Play(rc=1, log="e: file:///x/MainActivityTest.kt:3:1 Unresolved reference 'onView'.\n"))
    build, said = await rig.answer("test")
    assert build.state == "failed" and said[:2] == [
        "test FAILED · app (debug) · the tests did not build · 0s · 1 error, 0 warnings",
        "error /x/MainActivityTest.kt:3:1 Unresolved reference 'onView'.",
    ]
    rig.gradle.then(Play())
    build, said = await rig.answer("test")
    assert build.state == "succeeded" and said[0] == "test ok · app (debug) · 0 passed, 0 failed, 0 skipped · 0s"


async def test_results_an_earlier_run_left_are_not_read_as_this_ones(rig: Rig) -> None:
    rig.gradle.then(Play(rc=1, results=junit_results(("addsAnItem", FAILED))))
    await rig.answer("test")
    rig.gradle.then(Play(rc=1, log="* What went wrong:\nDevice went away\n"))
    _, said = await rig.answer("test")
    assert said[:2] == [
        "test FAILED · app (debug) · the tests did not build · 0s · 1 error, 0 warnings",
        "error Device went away",
    ]


async def test_a_test_run_held_to_an_app_builds_it_first_and_stops_on_another(rig: Rig) -> None:
    rig.gradle.then(Play(apk=True, package="dev.simmirror.other"))
    build, said = await rig.answer("test", expected_bundle_id=EXAMPLE_PACKAGE)
    assert rig.args() == [("--console=plain", ":app:assembleDebug")] and build.state == "failed"
    assert said[0] == (
        "test FAILED · app (debug) builds dev.simmirror.other, not dev.simmirror.example, the app "
        "build.expected_bundle_id names, so the tests were not run and nothing was installed: name the variant that "
        "builds dev.simmirror.example with configuration, or change build.expected_bundle_id"
    )
    rig.gradle.then(Play(apk=True)).then(Play(results=junit_results(("startsEmpty", None))))
    build, said = await rig.answer("test", expected_bundle_id=EXAMPLE_PACKAGE)
    assert [args[1] for args in rig.args()[1:]] == [":app:assembleDebug", ":app:connectedDebugAndroidTest"]
    assert build.state == "succeeded"
    rig.gradle.then(Play(rc=1, log="* What went wrong:\nCompilation error\n"))
    build, said = await rig.answer("test", expected_bundle_id=EXAMPLE_PACKAGE)
    assert len(rig.gradle.calls) == 4 and said[1] == "error Compilation error"


async def test_a_retry_that_cannot_start_ends_the_run(rig: Rig) -> None:
    rig.gradle.then(Play(rc=1, results=junit_results(("addsAnItem", FAILED))))

    async def start_once(args: Any, **options: Any) -> Any:
        if rig.gradle.calls:
            raise OSError("gone")
        return await FakeGradle.__call__(rig.gradle, args, **options)

    rig.toolchain._start = start_once
    build, said = await rig.answer("test", retries=1)
    assert build.process.returncode == CANNOT_RUN and said[1].endswith("once")


# -- what is installed -----------------------------------------------------------------------------------------------


async def test_an_emulators_apk_is_stamped_beside_it_and_a_phones_is_not(rig: Rig) -> None:
    rig.gradle.then(Play(apk=True))
    build, _ = await rig.answer()
    folder, said = await rig.runner.stamped(build)
    assert said == f"stamped {'c' * 7}, kept on the device beside the app (the APK is unchanged)"
    assert sorted(path.name for path in folder.iterdir()) == ["app-debug.apk", APK_STAMP]
    found = apk_stamp(folder)
    assert found is not None and found[0] == EXAMPLE_PACKAGE and found[1].commit == "c" * 40
    build.udid = PHONE
    assert await rig.runner.stamped(build) == (
        build.app,
        "not stamped: SimMirror writes nothing to an Android phone but the app it installs",
    )
    build.udid, build.commit = UDID, None
    assert (await rig.runner.stamped(build))[1] == f"not stamped: git named no commit for {rig.folder}"
    build.commit = "c" * 40
    assert build.app is not None
    build.app.unlink()
    path, said = await rig.runner.stamped(build)
    assert path == build.app and said.startswith("not stamped: app-debug.apk could not be copied and stamped")
    assert apk_stamp(build.app.parent) is None


async def test_the_wrapper_is_started_through_sh_in_the_project_with_its_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[tuple[list[str], Path, dict[str, Any]]] = []

    async def spawn(argv: list[str], log_path: Path, **options: Any) -> str:
        spawned.append((argv, log_path, options))
        return "process"

    monkeypatch.setattr(gradle, "spawn", spawn)
    monkeypatch.setenv("PATH", "/usr/bin")
    started = await start_gradle([":app:assembleDebug"], folder=tmp_path, log_path=tmp_path / "b.log", env={"A": "1"})
    ((argv, log_path, options),) = spawned
    assert started == "process" and argv == ["/bin/sh", str(tmp_path / "gradlew"), ":app:assembleDebug"]
    assert log_path == tmp_path / "b.log" and options["cwd"] == tmp_path
    assert options["env"]["A"] == "1" and options["env"]["PATH"] == "/usr/bin"


OFFLINE = "Exception thrown during onBeforeAll invocation of plugin AndroidTestApkInstallerPlugin: device offline\n"


async def test_a_test_run_the_device_dropped_before_any_test_ran_is_run_again_once_it_is_back(rig: Rig) -> None:
    rig.gradle.then(Play(rc=1, log=OFFLINE)).then(Play(results=junit_results(("startsEmpty", None))))
    build, said = await rig.answer("test")
    assert len(rig.gradle.calls) == 2 and build.state == "succeeded" and said[0].startswith("test ok")
    assert rig.waited == [0.5]
    # A device that does not come back: the run answers as it ended.
    rig.stays_away = True
    rig.gradle.then(Play(rc=1, log=OFFLINE))
    build, said = await rig.answer("test")
    assert len(rig.gradle.calls) == 3 and build.state == "failed" and len(rig.waited) == 31
    # Only once: a run dropped again answers as it ended.
    rig.stays_away, rig.reachable = False, True
    rig.gradle.then(Play(rc=1, log=OFFLINE)).then(Play(rc=1, log=OFFLINE))
    build, _ = await rig.answer("test")
    assert len(rig.gradle.calls) == 5 and build.state == "failed"
