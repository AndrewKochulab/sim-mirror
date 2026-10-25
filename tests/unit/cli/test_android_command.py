# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror android``: what is set up, the AVDs, fetching scrcpy's server and having a daemon start it, and
removing what SimMirror left on devices -- against a fake adb server, without the network or a daemon."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Sequence
from pathlib import Path

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.main import main
from sim_mirror.connectors.android.scrcpy import remote_jar
from sim_mirror.core.android_control import STAMPS
from sim_mirror.platform.adb import Adb
from sim_mirror.platform.java import STUDIO_JDK
from sim_mirror.scope import Scope
from sim_mirror.storage.app_support import AppSupportStateStore
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.android_sdk import avd, image, sdk_at
from sim_mirror.testing.daemon import Daemon
from sim_mirror.testing.scrcpy import TEST_JAR, TEST_RELEASE


class Here:
    """A terminal on a Mac with the Android SDK, an AVD and Android Studio's JDK, its adb server `server`."""

    def __init__(self, root: Path, *, settings: str = "", server: FakeAdbServer | None = None) -> None:
        self.root = root
        self.sdk = sdk_at(root / "Library/Android/sdk")
        system = image(self.sdk, "36")
        avd(root / ".android/avd", "Pixel_9", f"avd.ini.displayname=Pixel 9\nimage.sysdir.1={system}\n")
        jdk = root / "Applications" / STUDIO_JDK
        (jdk / "bin").mkdir(parents=True)
        (jdk / "bin" / "java").touch()
        (jdk / "release").write_text('JAVA_VERSION="21.0.9"\n')
        (root / "config.toml").write_text(f"[build]\ntools = true\n{settings}")
        self.fetched: list[str] = []
        self.jar = TEST_JAR
        self.daemon = Daemon(up=False)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.server = server
        self.ctx = CliContext(
            env={"SIM_MIRROR_STATE_DIR": str(root / "state"), "SIM_MIRROR_CONFIG": str(root / "config.toml")},
            cwd=root,
            stdout=self.out,
            stderr=self.err,
            stdin=io.StringIO(),
            home=root,
            run=self.run,
            opener=self.daemon,
            scrcpy_fetch=self.fetch,
            scrcpy_release=TEST_RELEASE,
            adb=self.adb,
            applications=root / "Applications",
        )
        self.daemon.admin = lambda: self.ctx.tokens().admin_token()

    async def run(self, argv: Sequence[str]) -> tuple[int, str]:
        return 1, ""

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        return self.jar

    def adb(self) -> Adb:
        return self.server.adb() if self.server is not None else Adb(str(self.root / "no-adb.sock"))

    def touch(self) -> str:
        return f"/api/v1/scopes/{Scope.for_folder(self.root).id}/device/touch"

    async def __call__(self, *argv: str) -> int:
        # Each command runs its own event loop, as `sim-mirror` does, beside the test's fake adb server.
        return await asyncio.to_thread(main, list(argv), ctx=self.ctx)

    def said(self) -> list[str]:
        return self.out.getvalue().splitlines()


async def test_status_says_what_is_set_up_as_the_doctor_does(tmp_path: Path) -> None:
    async with FakeAdbServer() as server:
        server.emulator()
        here = Here(tmp_path, server=server)
        assert await here("android") == 2
        said = here.said()
        assert said[0].endswith(
            f"android sdk: adb 37.0.0, emulator 36.4.9 at {here.sdk.root}; adb's server answers (protocol 41); "
            "AVDs: Pixel 9"
        )
        assert said[1].endswith("android devices: emulator-5554 (emulator, device)")
        assert said[2].endswith("scrcpy: scrcpy's server is not set up: set it up once (Set up touch, or sim-mirror "
                                "android setup); until then adb shows the screen a few times a second")  # fmt: skip
        assert said[-1].endswith(
            f"android builds: JDK 21.0.9 at {tmp_path / 'Applications' / STUDIO_JDK} (Android Studio)"
        )
        assert await here("android", "status", "--json") == 2
        report = json.loads("\n".join(here.said()[len(said) :]))
        assert [check["name"] for check in report["checks"]] == [
            "android sdk",
            "android devices",
            "scrcpy",
            "android builds",
        ]


async def test_avds_are_listed_with_what_stands_in_their_way(tmp_path: Path) -> None:
    here = Here(tmp_path)
    avd(tmp_path / ".android/avd", "Broken", "avd.ini.displayname=Broken\nimage.sysdir.1=system-images/android-99/x/\n")
    assert await here("android", "avds") == 0
    assert (
        here.said()[0].startswith("Broken  Broken  API 99  (its system image is not installed")
        and here.said()[1] == "Pixel_9  Pixel 9  API 36"
    )
    for path in (tmp_path / ".android/avd").glob("*.ini"):
        path.unlink()
    assert await here("android", "avds") == 0
    assert here.said()[-1] == (
        f"no Android virtual devices made with {here.sdk.root}: make one in Android Studio's Device Manager"
    )


async def test_setup_fetches_the_pinned_server_and_has_a_running_daemon_start_it(tmp_path: Path) -> None:
    here = Here(tmp_path)
    assert await here("android", "setup") == 0
    assert here.fetched == [TEST_RELEASE.url]
    kept = tmp_path / "state" / "scrcpy" / TEST_RELEASE.file_name
    assert kept.read_bytes() == TEST_JAR
    assert here.said()[1:] == [
        f"scrcpy's server 9.9.9 is set up at {kept}, checked by its SHA-256",
        "choose an Android device in the viewer, or with `sim-mirror devices choose`, to use it",
    ]
    here.daemon.up = True
    here.daemon.answers[("POST", here.touch())] = {"state": "starting"}
    assert await here("android", "setup") == 0 and len(here.fetched) == 1
    assert here.said()[-1] == "the daemon is starting it on the device" and here.daemon.made("POST", here.touch())
    here.daemon.answers[("POST", here.touch())] = {"state": "off"}
    assert await here("android", "setup") == 0
    assert here.said()[-1] == "choose an Android device in the viewer, or with `sim-mirror devices choose`, to use it"
    here.daemon.refuse.add(here.touch())
    assert await here("android", "setup") == 0
    assert here.said()[-1].startswith("the daemon did not start it (")


async def test_setup_says_when_scrcpy_is_off_or_another_server_is_named(tmp_path: Path) -> None:
    here = Here(tmp_path, settings='[android.scrcpy]\nenabled = false\npath = "/opt/scrcpy-server"\n')
    assert await here("android", "setup") == 0
    assert here.said()[-2:] == [
        "this scope uses /opt/scrcpy-server instead (android.scrcpy.path)",
        "it is off here: turn it on with `sim-mirror config set android.scrcpy.enabled true`",
    ]


async def test_a_server_that_is_not_the_pinned_release_is_thrown_away(tmp_path: Path) -> None:
    here = Here(tmp_path)
    here.jar = b"something else"
    assert await here("android", "setup") == 1
    assert "is not release 9.9.9" in here.err.getvalue()
    assert not (tmp_path / "state" / "scrcpy" / TEST_RELEASE.file_name).exists()


async def test_clean_removes_this_hosts_server_and_the_stamps_from_each_device(tmp_path: Path) -> None:
    async with FakeAdbServer() as server:
        server.emulator().phone().device("R58OFF", "unauthorized")
        here = Here(tmp_path, server=server)
        jar = remote_jar(AppSupportStateStore(here.ctx.env, tmp_path).owner_tag)
        assert await here("android", "clean") == 0
        assert here.said() == [f"emulator-5554: removed {jar} and {STAMPS}", f"R58M12ABC: removed {jar} and {STAMPS}"]
        assert server.commands("emulator-5554")[-2:] == [f"rm -f {jar}", f"rm -rf {STAMPS}"]
        assert server.commands("R58OFF") == []
        server.on("rm -f", code=1, err="rm: Read-only file system")
        assert await here("android", "clean") == 1
        assert "emulator-5554: rm: Read-only file system" in here.err.getvalue()
        for serial in list(server.devices):
            server.unplug(serial)
        assert await here("android", "clean") == 0 and here.said()[-1] == "no Android device is running or connected"


async def test_clean_without_adbs_server_has_nothing_to_remove(tmp_path: Path) -> None:
    here = Here(tmp_path)
    assert await here("android", "clean") == 0
    assert here.said() == ["adb's server is not running, so no device has anything of SimMirror's to remove"]
