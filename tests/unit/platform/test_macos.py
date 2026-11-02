# SPDX-License-Identifier: Apache-2.0
"""The selected bundle path survives rebuilds and duplicate identifiers; only its current PID is stopped."""

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


async def test_exact_selected_path_is_not_replaced_by_another_copy() -> None:
    fake = FakeMacOS(APP)
    assert await fake.mac.apps() == [APP]
    other = replace(APP, path="/Other/Sample.app", pid=102)
    fake.apps.append(other)
    assert await fake.mac.app(APP.id) == APP
    fake.apps = [other]
    assert (await fake.mac.app(APP.id)).pid is None
    assert await fake.mac.launch(APP.id, ["--sample"]) == 777
    assert fake.calls[-2] == ("open", "-g", "-n", "-a", APP.path, "--args", "--sample")
    assert await fake.mac.launch(APP.id) == 777
    assert await fake.mac.terminate(APP.id)
    assert fake.signals == [(777, 15)]
    assert other in fake.apps
    assert not await fake.mac.terminate(APP.id)
    with pytest.raises(DeviceControlError, match="not known"):
        await fake.mac.app("unknown")


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
