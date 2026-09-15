# SPDX-License-Identifier: Apache-2.0
"""The doctor's report -- lines to read, JSON to attach, an exit code to script on -- and what it asks macOS."""

from __future__ import annotations

from collections.abc import Sequence

from sim_mirror._version import __version__
from sim_mirror.doctor.macos import DEVICE_HUB_FIX, check_accessibility, check_device_hub, check_gui_session
from sim_mirror.doctor.report import CheckResult, Report
from sim_mirror.protocol import PROTOCOL_VERSION


def runs(answers: dict[tuple[str, ...], tuple[int, str]]):  # type: ignore[no-untyped-def]
    async def run(argv: Sequence[str]) -> tuple[int, str]:
        return answers.get(tuple(argv), (1, ""))

    return run


def test_a_report_fails_when_anything_failed_warns_when_something_warned_and_says_how_to_fix_it() -> None:
    ok = CheckResult("mac", "ok", "macOS 26.6.2")
    warned = CheckResult("idb_companion", "warn", "not installed", "brew install facebook/fb/idb-companion")
    failed = CheckResult("xcode", "fail", "no Xcode is selected", "Install Xcode")
    skipped = CheckResult("accessibility", "skip", "not checked")
    assert (Report((ok, skipped)).status, Report((ok, skipped)).exit_code) == ("ok", 0)
    assert (Report((ok, warned)).status, Report((ok, warned)).exit_code) == ("warn", 2)
    report = Report((ok, warned, failed))
    assert (report.status, report.exit_code) == ("fail", 1)
    assert report.text().splitlines() == [
        "ok    mac: macOS 26.6.2",
        "warn  idb_companion: not installed",
        "      fix: brew install facebook/fb/idb-companion",
        "FAIL  xcode: no Xcode is selected",
        "      fix: Install Xcode",
    ]
    assert report.to_dict() == {
        "sim_mirror": __version__,
        "protocol": PROTOCOL_VERSION,
        "status": "fail",
        "checks": [
            {"name": "mac", "status": "ok", "detail": "macOS 26.6.2", "fix": ""},
            {"name": "idb_companion", "status": "warn", "detail": "not installed", "fix": warned.fix},
            {"name": "xcode", "status": "fail", "detail": "no Xcode is selected", "fix": "Install Xcode"},
        ],
    }


async def test_an_open_device_hub_is_a_warning_with_what_to_do() -> None:
    open_hub = await check_device_hub(runs({("pgrep", "-x", "Device Hub"): (0, "812\n")}))
    assert (open_hub.status, open_hub.fix) == ("warn", DEVICE_HUB_FIX)
    helper = await check_device_hub(runs({("pgrep", "-x", "dtuhidd"): (0, "901\n")}))
    assert (helper.status, helper.detail) == ("ok", "Device Hub is not open (its input helper dtuhidd is running)")
    assert (await check_device_hub(runs({}))).detail == "Device Hub is not open"


async def test_simulators_need_a_desktop_session_and_accessibility_is_never_asked_about() -> None:
    aqua = await check_gui_session(runs({("launchctl", "managername"): (0, "Aqua\n")}))
    assert (aqua.status, aqua.detail) == ("ok", "a logged-in desktop session (Aqua)")
    background = await check_gui_session(runs({("launchctl", "managername"): (0, "Background\n")}))
    assert background.status == "warn" and "Background session" in background.detail and background.fix
    assert (await check_gui_session(runs({}))).status == "skip"
    assert (await check_gui_session(runs({("launchctl", "managername"): (0, "\n")}))).status == "skip"
    assert check_accessibility().status == "skip"
