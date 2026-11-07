# SPDX-License-Identifier: Apache-2.0
"""One device per bundle ID follows the copy that runs, from any folder; only its current PID is stopped."""

import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from sim_mirror.platform import macos
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.macos import MacApp, applications, bundle
from sim_mirror.testing.macos import FakeMacOS

APP = MacApp("dev.example.Sample", "Sample", "/Applications/Sample.app", 101)


def test_bundle_metadata_is_read_without_launching(tmp_path: Path) -> None:
    root = tmp_path / "Sample.app"
    (root / "Contents").mkdir(parents=True)
    path = root / "Contents/Info.plist"
    for info, name in [
        ({"CFBundleIdentifier": APP.bundle_id}, "Sample"),
        (
            {"CFBundleIdentifier": APP.bundle_id, "CFBundleName": "Example", "CFBundleShortVersionString": "2"},
            "Example",
        ),
    ]:
        path.write_bytes(plistlib.dumps(info))
        assert bundle(str(root)).name == name
    for info in [{}, {"CFBundleIdentifier": "../Bad"}, {"CFBundleIdentifier": 12}]:
        path.write_bytes(plistlib.dumps(info))
        with pytest.raises(DeviceControlError):
            bundle(str(root))
    path.unlink()
    with pytest.raises(DeviceControlError):
        bundle(str(root))
    assert applications('1) "Agent"\n bundleID="dev.example.agent"\n pid = 4 type="BackgroundOnly"') == []


async def test_a_launch_opens_the_copy_last_seen_running_and_never_another() -> None:
    fake = FakeMacOS(APP)
    assert await fake.mac.apps() == [APP]
    fake.apps = []
    assert (await fake.mac.app(APP.id)).pid is None
    assert await fake.mac.launch(APP.id, ["--sample"]) == 777
    assert fake.calls[-2] == ("open", "-g", "-n", "-a", APP.path, "--args", "--sample")
    assert await fake.mac.launch(APP.id) == 777
    assert await fake.mac.terminate(APP.id)
    assert fake.signals == [(777, 15)]
    assert not await fake.mac.terminate(APP.id)
    with pytest.raises(DeviceControlError, match="not known"):
        await fake.mac.app("unknown")


async def test_the_device_follows_a_copy_running_from_another_install_folder() -> None:
    fake = FakeMacOS(APP)
    await fake.mac.apps()
    fake.apps = []
    await fake.mac.apps()
    other = replace(APP, path="/Other/Sample.app", pid=102)
    fake.apps = [other]
    assert await fake.mac.apps() == [other]
    fake.apps = []
    assert await fake.mac.launch(APP.id) == 777
    assert fake.calls[-2] == ("open", "-g", "-n", "-a", other.path, "--args")


async def test_of_several_running_copies_the_frontmost_then_the_newest_is_followed() -> None:
    older = replace(APP, path="/Older/Sample.app", pid=201)
    newer = replace(APP, path="/Newer/Sample.app", pid=202)
    fake = FakeMacOS(newer, older)
    fake.launched = {201: "2026/11/08 00:33:48", 202: "2026/11/08 00:34:41"}
    assert await fake.mac.app(APP.id) == newer
    assert ("lsappinfo", "visibleProcessList") in fake.calls
    fake.visible = [999, 201, 202]
    assert await fake.mac.app(APP.id) == older
    fake.fail.add("lsappinfo visibleProcessList")
    assert await fake.mac.app(APP.id) == newer
    fake.launched = {}
    assert await fake.mac.app(APP.id) == older


async def test_a_held_session_keeps_its_copy_while_it_runs() -> None:
    first = replace(APP, pid=301)
    second = replace(APP, path="/Second/Sample.app", pid=302)
    fake = FakeMacOS(first, second)
    fake.mac.held[APP.id] = 301
    fake.visible = [302, 301]
    assert await fake.mac.app(APP.id) == first
    fake.apps = [second]
    assert await fake.mac.app(APP.id) == second


async def test_one_copy_running_asks_nothing_of_the_visible_list() -> None:
    fake = FakeMacOS(APP)
    await fake.mac.apps()
    assert fake.calls == [("lsappinfo", "list")]


def test_launch_services_listing_is_parsed_with_its_rank() -> None:
    text = (
        '99) "Sample" ASN:0x0-0xd9ab99e: \n'
        '    bundleID="dev.example.Sample"\n'
        '    bundle path="/Applications/Sample.app"\n'
        '    pid = 101 type="Foreground" flavor=3 Version="1.2" fileType="APPL"\n'
        "    launch time =  2026/11/08 00:33:48 ( 7 minutes, 53.7 seconds ago )\n"
        '100) "Sample" \n bundleID="dev.example.Sample"\n bundle path="/B/Sample.app"\n pid = 102 type="UIElement"\n'
        "    checkin time = 2026/11/04 12:27:44 ( 3 days ago )\n"
        '101) "Sample" \n bundleID="dev.example.Sample"\n bundle path="/C/Sample.app"\n pid = 103 type="UIElement"'
    )
    first, second, third = applications(text)
    assert first == macos.Running(replace(APP, version="1.2"), "0x0-0xd9ab99e", "2026/11/08 00:33:48", 0)
    assert second == macos.Running(replace(APP, path="/B/Sample.app", pid=102), "", "2026/11/04 12:27:44", 1)
    assert third == macos.Running(replace(APP, path="/C/Sample.app", pid=103), "", "", 2)
    assert macos.front_to_back('ASN:0x0-0x1-"Terminal": ASN:0x0-0xd9ab99e-"Sample": ') == {
        "0x0-0x1": 0,
        "0x0-0xd9ab99e": 1,
    }


async def test_an_installed_version_is_kept_while_its_copy_runs() -> None:
    fake = FakeMacOS(APP)
    fake.mac.known[APP.id] = replace(APP, pid=None, version="3")
    assert (await fake.mac.app(APP.id)).version == "3"


async def test_logs_are_confined_to_the_selected_pid() -> None:
    fake = FakeMacOS(APP)
    assert await fake.mac.logs(APP.id, 4) == ["app message"]
    assert fake.calls[-1][-1] == "processIdentifier == 101"
    fake.fail.add("log")
    with pytest.raises(DeviceControlError, match="log"):
        await fake.mac.logs(APP.id, 4)
    fake.apps = []
    assert await fake.mac.logs(APP.id, 4) == []


async def test_lifecycle_failure_never_falls_back_to_another_process(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast(_: float) -> None:
        pass

    monkeypatch.setattr(macos.asyncio, "sleep", fast)
    fake = FakeMacOS(APP)
    await fake.mac.apps()
    fake.stops = False
    with pytest.raises(DeviceControlError, match="did not quit"):
        await fake.mac.terminate(APP.id)
    fake.apps = []
    fake.starts = False
    with pytest.raises(DeviceControlError, match="did not appear"):
        await fake.mac.launch(APP.id)
    fake.fail.add("open")
    with pytest.raises(DeviceControlError, match="background"):
        await fake.mac.launch(APP.id)
    fake.fail.add("lsappinfo")
    with pytest.raises(DeviceControlError, match="list"):
        await fake.mac.apps()


async def test_process_disappearing_before_signal_is_already_stopped() -> None:
    fake = FakeMacOS(APP)

    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    fake.mac.signal_pid = gone
    assert not await fake.mac.terminate(APP.id)
    with pytest.raises(AssertionError):
        await fake.run(("unexpected",))
    with pytest.raises(ProcessLookupError):
        fake.signal_pid(999, 15)


async def test_selected_window_belongs_to_one_process_lifetime():
    fake = FakeMacOS(APP)
    await fake.mac.apps()
    fake.mac.windows[APP.id] = 7
    assert (await fake.mac.app(APP.id)).pid == APP.pid
    assert fake.mac.windows[APP.id] == 7
    fake.apps = [replace(APP, pid=202)]
    assert (await fake.mac.app(APP.id)).pid == 202
    assert APP.id not in fake.mac.windows
    fake.mac.windows[APP.id] = 8
    fake.apps = []
    assert (await fake.mac.app(APP.id)).pid is None
    assert APP.id not in fake.mac.windows
