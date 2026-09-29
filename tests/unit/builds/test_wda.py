# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent built for any iPhone under a bundle id of SimMirror's own, and run on the device's loopback."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

from sim_mirror.build.wda import (
    BUILD_TIMEOUT_S,
    build_argv,
    build_wda,
    configured,
    failure_of,
    run_argv,
    run_env,
    runner_bundle_id,
    start_wda,
    xctestrun,
)
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun

TEAM = "9Q48L5C2K5"
XCODE = "/Applications/Xcode.app/Contents/Developer"


def made(derived: Path, name: str = "WebDriverAgentRunner_iphoneos26.0-arm64.xctestrun") -> Path:
    products = derived / "Build" / "Products"
    products.mkdir(parents=True, exist_ok=True)
    (products / name).write_text("<plist/>")
    return products / name


def test_the_runner_is_built_for_any_iphone_under_a_bundle_id_of_simmirrors_own(tmp_path: Path) -> None:
    assert runner_bundle_id(TEAM) == "dev.simmirror.9q48l5c2k5.WebDriverAgentRunner"
    assert build_argv(tmp_path / "wda", tmp_path / "derived", TEAM) == (
        "xcodebuild", "build-for-testing", "-project", str(tmp_path / "wda" / "WebDriverAgent.xcodeproj"),
        "-scheme", "WebDriverAgentRunner", "-destination", "generic/platform=iOS",
        "-derivedDataPath", str(tmp_path / "derived"), "-allowProvisioningUpdates", "CODE_SIGN_STYLE=Automatic",
        f"DEVELOPMENT_TEAM={TEAM}", "PRODUCT_BUNDLE_IDENTIFIER=dev.simmirror.9q48l5c2k5.WebDriverAgentRunner",
    )  # fmt: skip


def test_a_named_device_is_built_for_so_xcode_registers_it_with_the_team(tmp_path: Path) -> None:
    argv = build_argv(tmp_path, tmp_path / "derived", TEAM, PHONE_UDID)
    assert argv[argv.index("-destination") + 1] == f"id={PHONE_UDID}"


async def test_a_build_answers_what_it_left_to_run_from(tmp_path: Path) -> None:
    derived = tmp_path / "derived"
    made(derived, "WebDriverAgentRunner_iphonesimulator26.0-arm64.xctestrun")
    expected = made(derived)
    fake = FakeXcrun()
    built = await build_wda(tmp_path / "wda", derived, TEAM, XCODE, xcrun=fake)
    assert built.xctestrun == expected and built.failure == "" and xctestrun(tmp_path) is None
    made(derived, "WebDriverAgentRunner_iphoneos26.0-arm64.00008120-0011223344556677.simmirror.xctestrun")
    assert xctestrun(derived) == expected, "a copy made for a run is not what a build left"
    (call,) = fake.calls
    assert call.args[1] == "build-for-testing" and call.timeout == BUILD_TIMEOUT_S and call.developer_dir == XCODE
    empty = await build_wda(tmp_path / "wda", tmp_path / "none", TEAM, XCODE, xcrun=fake)
    assert empty.xctestrun is None and "left no .xctestrun" in empty.failure


async def test_a_build_that_fails_says_what_xcodebuild_said_went_wrong(tmp_path: Path) -> None:
    said = (
        "note: Using codesigning identity override\n"
        '/wda/WebDriverAgent.xcodeproj: error: No Account for Team "9Q48L5C2K5". Add a new account in Accounts.\n'
        '/wda/WebDriverAgent.xcodeproj: error: No Account for Team "9Q48L5C2K5". Add a new account in Accounts.\n'
        "** TEST BUILD FAILED **\n"
    )
    fake = FakeXcrun().on("xcodebuild", "build-for-testing", rc=65, out=said)
    built = await build_wda(tmp_path, tmp_path / "derived", TEAM, XCODE, xcrun=fake)
    assert built.xctestrun is None
    assert built.failure == (
        '/wda/WebDriverAgent.xcodeproj: error: No Account for Team "9Q48L5C2K5". Add a new account in Accounts.'
    )
    assert failure_of("line one\n** BUILD FAILED **\n") == "** BUILD FAILED **" and failure_of("") == (
        "xcodebuild said nothing"
    )
    silent = await build_wda(tmp_path, tmp_path / "derived", TEAM, XCODE, xcrun=FakeXcrun().on("xcodebuild", rc=1))
    assert silent.failure  # what xcrun says of a call that failed and printed nothing


def plan(path: Path, layout: dict[str, Any]) -> Path:
    path.write_bytes(plistlib.dumps(layout))
    return path


def environments(path: Path) -> list[dict[str, str]]:
    layout = plistlib.loads(path.read_bytes())
    found = [value["EnvironmentVariables"] for key, value in layout.items() if key == "WebDriverAgentRunner"]
    for configuration in layout.get("TestConfigurations", []):
        found += [target["EnvironmentVariables"] for target in configuration["TestTargets"]]
    return found


async def test_the_runner_serves_only_on_the_devices_loopback(tmp_path: Path) -> None:
    assert run_argv(tmp_path / "r.xctestrun", PHONE_UDID) == (
        "xcodebuild", "test-without-building", "-xctestrun", str(tmp_path / "r.xctestrun"),
        "-destination", f"id={PHONE_UDID}",
    )  # fmt: skip
    assert run_env(TEAM) == {
        "USE_PORT": "8100",
        "MJPEG_SERVER_PORT": "9100",
        "USE_IP": "127.0.0.1",
        "WDA_PRODUCT_BUNDLE_IDENTIFIER": runner_bundle_id(TEAM),
    }
    seen: dict[str, Any] = {}

    async def start(*args: str, **options: Any) -> str:
        seen.update(args=args, **options)
        return "process"

    built = plan(
        tmp_path / "WebDriverAgentRunner_iphoneos26.5-arm64.xctestrun",
        {
            "WebDriverAgentRunner": {"EnvironmentVariables": {"USE_IP": "", "TERM": "dumb"}, "TestBundlePath": "x"},
            "__xctestrun_metadata__": {"FormatVersion": 1},
        },
    )
    log = tmp_path / "wda.log"
    assert await start_wda(built, PHONE_UDID, TEAM, XCODE, log, start=start) == "process"
    run = Path(seen["args"][3])
    assert run.parent == built.parent and run.name.endswith(f".{PHONE_UDID}.simmirror.xctestrun")
    assert seen["args"] == run_argv(run, PHONE_UDID) and seen["log_path"] == log and seen["developer_dir"] == XCODE
    assert environments(run) == [{"TERM": "dumb", **run_env(TEAM)}]
    assert environments(built) == [{"USE_IP": "", "TERM": "dumb"}], "what the build left is left as it was"


def test_either_of_xcodes_layouts_gets_the_runners_environment(tmp_path: Path) -> None:
    built = plan(
        tmp_path / "r.xctestrun",
        {
            "TestConfigurations": [{"TestTargets": [{"TestBundlePath": "x"}, {"BlueprintName": "not a target"}]}],
            "__xctestrun_metadata__": {"FormatVersion": 2},
        },
    )
    layout = plistlib.loads(configured(built, PHONE_UDID, {"USE_IP": "127.0.0.1"}).read_bytes())
    targets = layout["TestConfigurations"][0]["TestTargets"]
    assert targets[0]["EnvironmentVariables"] == {"USE_IP": "127.0.0.1"} and "EnvironmentVariables" not in targets[1]
