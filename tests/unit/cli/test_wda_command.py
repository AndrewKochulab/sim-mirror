# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror wda``: teams, setting WebDriverAgent up with one, what is set up, and removing it -- without Xcode."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.main import main
from sim_mirror.connectors.iphone import wda_source
from sim_mirror.connectors.iphone.wda import derived_for
from sim_mirror.connectors.iphone.wda_source import MJPEG_ON_LOOPBACK, WdaRelease
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun, fixture
from sim_mirror.testing.wda import COMMIT, FOLDER, wda_archive

TEAM = "TESTTEAM01"
XCODE = "/Applications/Xcode27.app/Contents/Developer"
PEM = fixture("development-certificate.pem")
ARCHIVE = wda_archive()


class Here:
    """A terminal with a config, a keychain and an Xcode that answer as a test says."""

    def __init__(self, root: Path, *, team: str = TEAM, settings: str = "") -> None:
        self.root = root
        self.state = root / "state"
        self.xcrun = FakeXcrun()
        self.out, self.err = io.StringIO(), io.StringIO()
        self.keychain = (0, PEM)
        self.fetched: list[str] = []
        team_line = f'team_id = "{team}"\n' if team else ""
        (root / "config.toml").write_text(f'[device]\ndeveloper_dir = "{XCODE}"\n[real_devices]\n{team_line}{settings}')
        self.ctx = CliContext(
            env={"SIM_MIRROR_STATE_DIR": str(self.state), "SIM_MIRROR_CONFIG": str(root / "config.toml")},
            cwd=root,
            stdout=self.out,
            stderr=self.err,
            stdin=io.StringIO(),
            home=root,
            run=self.run,
            xcrun=self.xcrun,
            fetch=self.fetch,
        )

    async def run(self, argv: Sequence[str]) -> tuple[int, str]:
        assert tuple(argv[:2]) == ("security", "find-certificate")
        return self.keychain

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        return ARCHIVE

    def derived(self) -> Path:
        return derived_for(self.state / "wda", TEAM, XCODE)

    def builds(self) -> None:
        """Have xcodebuild leave what a build leaves."""
        products = self.derived() / "Build" / "Products"

        def built(args: tuple[str, ...]) -> XcrunResult:
            products.mkdir(parents=True, exist_ok=True)
            (products / "WebDriverAgentRunner_iphoneos26.5-arm64.xctestrun").write_text("<plist/>")
            return XcrunResult(0, "** TEST BUILD SUCCEEDED **", "")

        self.xcrun.on("xcodebuild", "build-for-testing", then=built)

    def __call__(self, *argv: str) -> int:
        return main(list(argv), ctx=self.ctx)

    def said(self) -> str:
        return self.out.getvalue()


@pytest.fixture(autouse=True)
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The release SimMirror pins is the test's archive, so nothing is fetched from GitHub."""
    release = WdaRelease("9.9.9", COMMIT, hashlib.sha256(ARCHIVE).hexdigest())
    monkeypatch.setattr(wda_source, "PINNED", release)
    monkeypatch.setattr("sim_mirror.cli.wda.PINNED", release)


def test_the_teams_here_are_listed_with_their_certificates_expiry(tmp_path: Path) -> None:
    here = Here(tmp_path)
    assert here("wda", "teams") == 0 and here.said() == "TESTTEAM01  Test Team  (until 2126-09-05)\n"
    here.keychain = (44, "")
    assert here("wda", "teams") == 1 and "no Apple Development certificate" in here.err.getvalue()


def test_setup_fetches_checks_changes_and_builds_it_then_says_what_to_do_on_the_device(tmp_path: Path) -> None:
    here = Here(tmp_path)
    here.builds()
    assert here("wda", "setup") == 0
    said = here.said()
    assert "fetching WebDriverAgent 9.9.9" in said and "built WebDriverAgent as dev.simmirror.testteam01" in said
    assert "Enable UI Automation" in said and "real_devices.wda.enabled true" in said
    source = here.state / "wda" / "source" / FOLDER
    assert MJPEG_ON_LOOPBACK.new in (source / MJPEG_ON_LOOPBACK.path).read_text()
    (call,) = here.xcrun.calls
    assert call.args[:2] == ("xcodebuild", "build-for-testing") and call.developer_dir == XCODE
    assert f"-derivedDataPath={here.derived()}" not in call.args and str(here.derived()) in call.args
    assert here("wda", "setup", "--device", PHONE_UDID) == 0 and len(here.fetched) == 1, "the checked source is kept"
    assert f"id={PHONE_UDID}" in here.xcrun.calls[-1].args and here.said().count("fetching") == 1
    assert here("wda", "setup", "--device", "not-a-udid") == 1 and "is not a real device's UDID" in here.err.getvalue()


def test_setup_says_what_stops_it(tmp_path: Path) -> None:
    unset = Here(tmp_path, team="")
    assert unset("wda", "setup") == 1 and "real_devices.team_id" in unset.err.getvalue()
    here = Here(tmp_path)
    here.keychain = (0, "")
    here.xcrun.on("xcodebuild", "build-for-testing", rc=65, out='error: No Account for Team "TESTTEAM01".\n')
    assert here("wda", "setup") == 1
    assert "no certificate on this Mac signs for TESTTEAM01" in here.said()
    assert 'did not build: error: No Account for Team "TESTTEAM01".' in here.err.getvalue()
    assert "Xcode > Settings > Accounts" in here.err.getvalue()
    (tmp_path / "own").mkdir()
    elsewhere = Here(tmp_path / "own", settings=f'[real_devices.wda]\npath = "{tmp_path / "missing"}"\n')
    assert elsewhere("wda", "setup") == 1 and "holds no WebDriverAgent.xcodeproj" in elsewhere.err.getvalue()
    assert elsewhere.fetched == []


def test_status_says_what_is_set_up(tmp_path: Path) -> None:
    here = Here(tmp_path)
    assert here("wda", "status") == 1
    assert "WebDriverAgent: off" in here.said() and "built: no" in here.said()
    here.builds()
    assert here("wda", "setup") == 0
    on = Here(tmp_path, settings="[real_devices.wda]\nenabled = true\n")
    assert on("wda", "status", "--json") == 0
    status = json.loads(on.said())
    assert status["enabled"] is True and status["team_id"] == TEAM and status["built"].endswith(".xctestrun")
    assert status["bundle_id"] == "dev.simmirror.testteam01.WebDriverAgentRunner"
    (tmp_path / "bare").mkdir()
    bare = Here(tmp_path / "bare", team="")
    assert bare("wda") == 1 and "team: not set" in bare.said()


def test_uninstall_removes_the_runner_this_team_built(tmp_path: Path) -> None:
    here = Here(tmp_path)
    here.xcrun.with_devicectl()
    assert here("wda", "uninstall", PHONE_UDID) == 0
    argv = here.xcrun.calls[-1].args
    runner = "dev.simmirror.testteam01.WebDriverAgentRunner.xctrunner"
    assert argv[argv.index("--device") + 1 :][:2] == (PHONE_UDID, runner)
    assert here("wda", "uninstall", "not-a-udid") == 1
    here.xcrun.on("devicectl", rc=1, err="the app is not installed")
    assert here("wda", "uninstall", PHONE_UDID) == 1 and "could not be removed" in here.err.getvalue()
