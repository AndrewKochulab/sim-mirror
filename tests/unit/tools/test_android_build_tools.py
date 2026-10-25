# SPDX-License-Identifier: Apache-2.0
"""The build tools on an Android device: Gradle builds the app, which is installed -- stamped on an emulator -- and
launched, its tests run on the device, and an app on a phone says it carries no stamp."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from sim_mirror.build.gradle import GradleToolchain
from sim_mirror.build.source import Source
from sim_mirror.build.stamp import APK_STAMP, STAMPED
from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.control import InstalledApp
from sim_mirror.platform.java import Jdk
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import (
    ANDROID_PHONE_UDID,
    EMULATOR_UDID,
    FULL_CONTROL,
    FakeAndroidBackend,
    FakeConnector,
    no_wait,
)
from sim_mirror.testing.gradle import EXAMPLE_PACKAGE, FakeGradle, Play, android_project, junit_results
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")
REGISTRY = ToolRegistry()
JDK = Jdk(Path("/opt/jdk"), "android.java_home")
SDK = "/Users/dev/Library/Android/sdk"


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


class AndroidBuildRig:
    def __init__(self, tmp_path: Path) -> None:
        self.androids = FakeAndroidBackend()
        self.sim = DeviceRig(
            tmp_path,
            androids=self.androids,
            android=FakeConnector("android", capabilities=FULL_CONTROL, kinds=frozenset({"android"})),
        )
        self.sim.config.set(android=True, android_phones=True, android_sdk_path=SDK, android_java_home="/opt/jdk")
        self.sim.memory.choose(CALLER.scope, False, EMULATOR_UDID)
        self.folder = android_project(tmp_path / "Groceries")
        self.gradle = FakeGradle()
        self.jdks: list[str] = []
        toolchain = GradleToolchain(self.serial_of, start=self.gradle, jdk=self.jdk)
        self.runner = BuildRunner(
            self.sim.state, xcrun=self.sim.xcrun, source=self.source, others={"android": toolchain}
        )
        self.actions = AgentActions(self.sim.manager, self.sim.config, clock=self.sim.clock, sleep=no_wait)

    async def serial_of(self, udid: str) -> str:
        return "emulator-5554"

    async def jdk(self, configured: str) -> Jdk:
        self.jdks.append(configured)
        return JDK

    async def source(self, folder: Path) -> Source:
        return Source(f"source {folder} · main @ 1a2b3c4", "1a2b3c4d" * 5)

    async def call(self, name: str, arguments: dict[str, Any]) -> Result:
        config = dataclasses.replace(self.sim.config.get(CALLER.scope), build_tools=True, build_stamp_source=True)
        ctx = ToolContext(
            manager=self.sim.manager,
            actions=self.actions,
            caller=CALLER,
            config=config,
            sleep=no_wait,
            builds=self.runner,
            folder=self.folder,
            shells_allowed=True,
        )
        return await REGISTRY.call(name, arguments, ctx)


async def test_an_android_app_is_built_with_gradle_stamped_installed_and_launched(tmp_path: Path) -> None:
    rig = AndroidBuildRig(tmp_path)
    rig.gradle.then(Play(apk=True))
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[:4] == [
        "build ok · app (debug) · 0s · 0 errors, 0 warnings",
        "installed app-debug.apk",
        "stamped 1a2b3c4, kept on the device beside the app (the APK is unchanged)",
        f"launched {EXAMPLE_PACKAGE} (pid 4242)",
    ]
    assert rig.gradle.calls[0].env == {"JAVA_HOME": "/opt/jdk", "ANDROID_HOME": SDK} and rig.jdks == ["/opt/jdk"]
    control = rig.androids.control_for
    install = next(call for call in control.calls if call[0] == "install")
    assert install[1] == EMULATOR_UDID and Path(install[2]).parent.name == STAMPED
    # The stamped folder is the device's to keep; the Mac's copy is gone once installed.
    assert not Path(install[2]).exists() and not (Path(install[2]) / APK_STAMP).exists()


async def test_an_android_build_that_leaves_no_apk_says_so(tmp_path: Path) -> None:
    rig = AndroidBuildRig(tmp_path)
    lines = said(await rig.call("sim_build_run", {})).splitlines()
    assert lines[1] == (
        "not launched: app (debug) left no APK its output-metadata.json names, so there is nothing to install; the "
        "log says what Gradle built"
    )


async def test_android_tests_run_on_the_scopes_emulator_and_another_running_one(tmp_path: Path) -> None:
    rig = AndroidBuildRig(tmp_path)
    rig.gradle.then(Play(results=junit_results(("startsEmpty", None))))
    assert said(await rig.call("sim_test", {})).startswith("test ok · app (debug) · 1 passed")
    assert rig.gradle.calls[0].env["ANDROID_SERIAL"] == "emulator-5554"
    # A device another scope is using is not one to send tests to.
    other = Caller(scope("tp-2"), key="agent-2", title="Claude · other")
    rig.sim.memory.choose(other.scope, False, ANDROID_PHONE_UDID)
    await rig.sim.manager.ensure(other.scope)
    refused = said(await rig.call("sim_test", {"destination": {"udid": ANDROID_PHONE_UDID}}))
    assert "is another project's device here; name one nobody is using" in refused


async def test_a_phones_app_says_it_carries_no_stamp(tmp_path: Path) -> None:
    rig = AndroidBuildRig(tmp_path)
    rig.sim.memory.choose(CALLER.scope, False, ANDROID_PHONE_UDID)
    rig.androids.control_for.apps[EXAMPLE_PACKAGE] = InstalledApp(EXAMPLE_PACKAGE, "example", "1.0", "/data/app", None)
    answer = said(await rig.call("sim_app", {"action": "info", "bundle_id": EXAMPLE_PACKAGE}))
    assert answer.splitlines()[1] == "no stamp: an app on an Android phone is never stamped"


async def test_android_tests_go_only_to_a_running_android_device(tmp_path: Path) -> None:
    rig = AndroidBuildRig(tmp_path)
    rig.sim.memory.choose(CALLER.scope, False, ANDROID_PHONE_UDID)
    refused = said(await rig.call("sim_test", {"destination": {"name": "Pixel 9"}}))
    assert refused.startswith("there is no Android device Pixel 9 running here; there are Pixel 8")
    usage = said(await rig.call("sim_test", {"destination": {"name": ""}}))
    assert usage.startswith('destination is {"name": "Pixel 9"}')
