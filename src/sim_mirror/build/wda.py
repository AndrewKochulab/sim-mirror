# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent built and run with xcodebuild, as Appium runs it -- kept here beside the build runner, the only
other place xcodebuild is named.

It is built once for a signing team and an Xcode (``build-for-testing``) -- for the device named, which Xcode then
registers with the team so its profile covers it, else for any iPhone the team's profile already covers -- under a
bundle id of SimMirror's own made from the team, so it never takes the name of an app already on the device; Xcode
makes the development profile it needs through the account signed in to it. It is run on one device from what that build
left (``test-without-building``), told to serve only on the device's own loopback -- ``USE_IP=127.0.0.1`` -- which the
Mac reaches through the cable, so nothing on the device's network can. That is written into a copy of the
``.xctestrun`` the build left, which is where Xcode reads a test runner's environment from. The run lasts as long as
the process does; ending the process group ends WebDriverAgent.
"""

from __future__ import annotations

import plistlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun, start_xcrun

#: The scheme that builds WebDriverAgent's test runner, and the project it is in.
RUNNER_SCHEME = "WebDriverAgentRunner"
PROJECT = "WebDriverAgent.xcodeproj"
#: How long building it may take: a first build compiles all of it, about two minutes.
BUILD_TIMEOUT_S = 900.0
#: The ports WebDriverAgent serves its API and its MJPEG stream on, on the device.
HTTP_PORT = 8100
MJPEG_PORT = 9100
#: What ends the name of a copy made for one device's run (`configured`).
COPY_SUFFIX = ".simmirror.xctestrun"
#: The most lines of what xcodebuild said that a failed build is explained with.
FAILURE_LINES = 3

Start = Callable[..., Awaitable[Any]]


def runner_bundle_id(team: str) -> str:
    """The bundle id WebDriverAgent's runner is built under for a team: SimMirror's own, never another app's."""
    return f"dev.simmirror.{team.lower()}.WebDriverAgentRunner"


def build_argv(source: Path, derived: Path, team: str, udid: str | None = None) -> tuple[str, ...]:
    return (
        "xcodebuild",
        "build-for-testing",
        "-project",
        str(source / PROJECT),
        "-scheme",
        RUNNER_SCHEME,
        "-destination",
        f"id={udid}" if udid else "generic/platform=iOS",
        "-derivedDataPath",
        str(derived),
        "-allowProvisioningUpdates",
        "CODE_SIGN_STYLE=Automatic",
        f"DEVELOPMENT_TEAM={team}",
        f"PRODUCT_BUNDLE_IDENTIFIER={runner_bundle_id(team)}",
    )


def xctestrun(derived: Path) -> Path | None:
    """What a build for devices left to run it from, or None."""
    products = derived / "Build" / "Products"
    built = [path for path in products.glob("*iphoneos*.xctestrun") if not path.name.endswith(COPY_SUFFIX)]
    return min(built, default=None)


@dataclass(frozen=True)
class WdaBuild:
    """What building WebDriverAgent left to run it from, or what xcodebuild said went wrong."""

    xctestrun: Path | None
    failure: str = ""


def failure_of(said: str) -> str:
    """The lines of what xcodebuild said that say what went wrong -- signing, most often -- else its last line."""
    lines = [line.strip() for line in said.splitlines() if line.strip()]
    errors = [line for line in lines if "error:" in line]
    return " ".join(dict.fromkeys(errors[-FAILURE_LINES:])) or (lines[-1] if lines else "xcodebuild said nothing")


async def build_wda(
    source: Path,
    derived: Path,
    team: str,
    developer_dir: str,
    *,
    udid: str | None = None,
    xcrun: XcrunRunner = run_xcrun,
) -> WdaBuild:
    """Build WebDriverAgent signed by `team`: for the device `udid` names, or for any iPhone."""
    argv = build_argv(source, derived, team, udid)
    built = await xcrun(*argv, timeout=BUILD_TIMEOUT_S, developer_dir=developer_dir)
    if not built.ok:
        return WdaBuild(None, failure_of(f"{built.out}\n{built.err}") if built.out or built.err else built.message)
    found = xctestrun(derived)
    return WdaBuild(found, "" if found else "the build finished but left no .xctestrun to run WebDriverAgent from")


def run_argv(test_run: Path, udid: str) -> tuple[str, ...]:
    return ("xcodebuild", "test-without-building", "-xctestrun", str(test_run), "-destination", f"id={udid}")


def run_env(team: str) -> dict[str, str]:
    """What WebDriverAgent is told as it starts: its ports, its bundle id, and to serve only on the device's own
    loopback."""
    return {
        "USE_PORT": str(HTTP_PORT),
        "MJPEG_SERVER_PORT": str(MJPEG_PORT),
        "USE_IP": "127.0.0.1",
        "WDA_PRODUCT_BUNDLE_IDENTIFIER": runner_bundle_id(team),
    }


def configured(test_run: Path, udid: str, env: Mapping[str, str]) -> Path:
    """A copy of what a build left to run from, beside it -- its paths are relative to its folder -- with
    WebDriverAgent's environment set in each test target, whichever of Xcode's two layouts it has."""
    with test_run.open("rb") as source:
        plan = plistlib.load(source)
    targets = [value for key, value in plan.items() if not key.startswith("__") and isinstance(value, dict)]
    for configuration in plan.get("TestConfigurations") or ():
        targets += [target for target in configuration.get("TestTargets") or () if isinstance(target, dict)]
    for target in targets:
        if isinstance(target.get("EnvironmentVariables"), dict) or "TestBundlePath" in target:
            target["EnvironmentVariables"] = {**(target.get("EnvironmentVariables") or {}), **env}
    copy = test_run.with_name(f"{test_run.stem}.{udid}{COPY_SUFFIX}")
    copy.write_bytes(plistlib.dumps(plan))
    return copy


async def start_wda(
    test_run: Path,
    udid: str,
    team: str,
    developer_dir: str,
    log_path: Path,
    *,
    start: Start = start_xcrun,
) -> Any:
    """Start WebDriverAgent on a device, in a process group of its own, its output appended to `log_path`."""
    run = configured(test_run, udid, run_env(team))
    return await start(*run_argv(run, udid), log_path=log_path, developer_dir=developer_dir)
