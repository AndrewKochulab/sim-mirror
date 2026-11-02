# SPDX-License-Identifier: Apache-2.0
"""App-scoped lifecycle keeps other bundles and pre-existing processes outside its reach."""

import plistlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.core.devices import NoDevice
from sim_mirror.core.mac_backend import MacBackend, MacControl
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.macos import MacApp
from sim_mirror.testing.macos import FakeMacOS
from sim_mirror.testing.rig import scope

APP = MacApp("dev.example.Sample", "Sample", "/Applications/Sample.app", 101)
CONFIG = SimConfig.defaults()


async def test_backend_lists_resolves_and_never_adopts_another_app() -> None:
    fake = FakeMacOS(APP)
    backend = MacBackend(fake.mac)
    instance = SimpleNamespace(udid=APP.id, booted_by_us=False)
    assert backend.kind == "mac" and backend.runs_here(instance)
    assert backend.developer_dir(CONFIG) == CONFIG.developer_dir
    assert isinstance(backend.control(""), MacControl)
    assert backend.withheld(instance, CONFIG) is None
    off = CONFIG.with_values(mac_apps=False)
    assert backend.withheld(instance, off)
    assert await backend.choices(off, ()) == []
    choices = await backend.choices(CONFIG, ())
    assert len(choices) == 1 and choices[0]["kind"] == "mac"
    assert (await backend.resolve(scope(), APP.id, CONFIG)).udid == APP.id
    with pytest.raises(NoDevice):
        await backend.resolve(scope(), None, CONFIG)
    await backend.prepare(instance, CONFIG)
    assert not instance.booted_by_us
    assert await backend.gone([instance]) == []
    fake.mac.windows[APP.id] = 7
    await backend.release(instance, shutdown=False)
    assert APP.id not in fake.mac.windows
    assert fake.signals == []
    fake.apps = []
    assert await backend.gone([instance]) == [instance]
    await backend.prepare(instance, CONFIG)
    assert instance.booted_by_us
    assert fake.signals == []
    await backend.release(instance, shutdown=True)
    assert fake.signals == [(777, 15)]
    await backend.prepare(instance, CONFIG)
    fake.fail.add("lsappinfo")
    assert await backend.gone([instance]) == []
    await backend.release(instance, shutdown=True)


async def test_restarted_process_is_not_ended_as_one_we_launched() -> None:
    fake = FakeMacOS(APP)
    fake.mac.started[APP.id] = 55
    await MacBackend(fake.mac).release(SimpleNamespace(udid=APP.id, booted_by_us=True), shutdown=True)
    assert fake.signals == []


async def test_control_is_bound_to_the_bundle_and_exact_path(tmp_path: Path) -> None:
    fake = FakeMacOS(APP)
    control = MacControl(fake.mac)
    assert (await control.app_info(APP.id, APP.bundle_id)).bundle_id == APP.bundle_id
    assert await control.launch(APP.id, APP.bundle_id) == 101
    assert await control.launch(APP.id, APP.bundle_id, ["--sample"], terminate_running=True) == 777
    assert await control.logs(APP.id, since_s=4, bundle_id=APP.bundle_id) == ["app message"]
    assert await control.logs(APP.id, since_s=4, bundle_id=None) == ["app message"]
    assert await control.display(APP.id) is not None
    with pytest.raises(DeviceControlError, match="another app"):
        await control.launch(APP.id, "dev.example.Other")
    root = tmp_path / "Sample.app"
    (root / "Contents").mkdir(parents=True)
    (root / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": APP.bundle_id}))
    with pytest.raises(DeviceControlError, match="terminate"):
        await control.install(APP.id, str(root))
    assert await control.terminate(APP.id, APP.bundle_id)
    await control.install(APP.id, str(root))
    assert fake.mac.known[APP.id].path == str(root)
    fake.apps = [replace(APP, path=str(root))]
    await control.install(APP.id, str(root))


@pytest.mark.parametrize(
    ("method", "args", "why"),
    [
        ("openurl", ("https://example.test",), "another app"),
        ("pbcopy", ("text",), "shared"),
        ("appearance", ("dark",), "SimMirrorKit"),
        ("text_size", ("large",), "system-wide"),
        ("contrast", (True,), "system-wide"),
        ("reduce_motion", (True,), "system-wide"),
        ("demo_status_bar", (), "status bar"),
        ("clear_status_bar", (), "status bar"),
        ("locate", (0, 0), "location"),
        ("route", ([(0, 0), (1, 1)], 1), "location"),
        ("clear_location", (), "location"),
    ],
)
async def test_out_of_process_operations_refuse_without_side_effects(method: str, args: tuple, why: str) -> None:
    fake = FakeMacOS(APP)
    with pytest.raises(DeviceControlError, match=why):
        await getattr(MacControl(fake.mac), method)(APP.id, *args)
    assert not fake.calls and not fake.signals


async def test_explicit_shutdown_quits_the_selected_process_but_not_a_replacement():
    fake = FakeMacOS(APP)
    backend = MacBackend(fake.mac)
    instance = SimpleNamespace(udid=APP.id, booted_by_us=False)
    await backend.prepare(instance, CONFIG)
    await backend.release(instance, shutdown=True)
    assert fake.signals == [(101, 15)]
    await backend.prepare(instance, CONFIG)
    fake.apps = [replace(APP, pid=888)]
    await backend.release(instance, shutdown=True)
    assert fake.signals == [(101, 15)]
