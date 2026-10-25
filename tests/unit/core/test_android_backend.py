# SPDX-License-Identifier: Apache-2.0
"""Android devices as a kind: AVDs and the emulators running them, phones, picking one, starting an emulator and
waiting for it to boot, letting it go, and noticing one gone."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.core.android_backend import AndroidBackend, choice_of
from sim_mirror.core.android_devices import AndroidDevice, AndroidDirectory, AndroidServices, runtime_label
from sim_mirror.core.devices import DeviceRef, NoDevice
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.adb import AdbError
from sim_mirror.platform.android_sdk import AndroidSdk
from sim_mirror.platform.emulator import EmulatorConsole, EmulatorError, Emulators
from sim_mirror.platform.identifiers import android_id
from sim_mirror.testing.adb import FakeAdbServer, FakeEmulatorConsole
from sim_mirror.testing.android_sdk import avd, image, sdk_at
from sim_mirror.testing.rig import scope

PIXEL = android_id("avd", "Pixel_9")
PHONE = android_id("adb", "R58M12ABC")
ON = SimConfig.defaults().with_values(android=True, android_phones=True)
SCOPE = scope("tp-1")


def make_sdk(root: Path) -> AndroidSdk:
    sdk = sdk_at(root)
    image(sdk, "36")
    return sdk


def make_avd(home: Path, name: str, *, whole: bool = True) -> None:
    system = "system-images/android-36/google_apis/arm64-v8a/" if whole else "system-images/android-99/none/"
    avd(home, name, f"avd.ini.displayname={name.replace('_', ' ')}\nimage.sysdir.1={system}\n")


def closed_port() -> int:
    """A port nothing listens on: one just let go."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Spawned:
    """The emulator's command, played: it starts, and adb lists it once it has booted."""

    def __init__(self, server: FakeAdbServer) -> None:
        self.server = server
        self.calls: list[Sequence[str]] = []

    async def __call__(self, command: Sequence[str], log: Path, **options: Any) -> Any:
        self.calls.append(command)
        port = command[command.index("-port") + 1]
        self.server.device(f"emulator-{port}", transport_id="9")
        return type("Process", (), {"pid": 777})()


class Rig:
    def __init__(self, root: Path, server: FakeAdbServer, console: FakeEmulatorConsole) -> None:
        self.server = server
        self.console = console
        self.sdk = make_sdk(root / "sdk")
        self.avds = root / "avd"
        make_avd(self.avds, "Pixel_9")
        make_avd(self.avds, "Broken", whole=False)
        self.spawned = Spawned(server)
        running: dict[int, str] = {}
        self.running = running

        async def command_of(pid: int) -> str | None:
            return running.get(pid)

        emulators = Emulators(
            root / "run", root / "logs", "host-a", spawn=self.spawned, alive=running.__contains__, command_of=command_of
        )
        adb = server.adb()
        directory = AndroidDirectory(adb, avds_of=self._avds)
        self.services = AndroidServices(adb, directory, emulators, sdk_path=str(self.sdk.root))
        self.now = 0.0
        self.slept: list[float] = []

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self.now += seconds

        #: Whether the emulator's console has gone with its emulator.
        self.console_gone = False

        def console_for(port: int) -> EmulatorConsole:
            return EmulatorConsole(closed_port() if self.console_gone else console.port, token=lambda: console.token)

        self.backend = AndroidBackend(self.services, clock=lambda: self.now, sleep=sleep, console_for=console_for)
        self.config = ON.with_values(android_sdk_path=str(self.sdk.root))

    def _avds(self, sdk: AndroidSdk | None) -> Any:
        from sim_mirror.platform.android_sdk import avds

        return avds(sdk, {"ANDROID_AVD_HOME": str(self.avds)})

    def emulator_up(self, serial: str = "emulator-5554", avd: str = "Pixel_9") -> None:
        self.server.device(serial, transport_id="1")
        self.server.on("getprop ro.boot.qemu.avd_name", out=f"{avd}\n16\n36\n")
        self.server.on("getprop sys.boot_completed", out="1\n")

    def phone(self, state: str = "device") -> None:
        self.server.device("R58M12ABC", state, model="Pixel_8", transport_id="2")
        self.server.on("getprop ro.serialno", out="R58M12ABC\n15\n35\n")

    def instance(self, udid: str = PIXEL, connection: Any = None) -> DeviceInstance:
        return DeviceInstance(
            udid=udid, name="Pixel 9", runtime="API 36", owner=SCOPE, developer_dir="", scopes={SCOPE.id},
            created=False, connector="android", capabilities=frozenset(), kind="android", connection=connection,
        )  # fmt: skip


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    async with FakeAdbServer() as server, FakeEmulatorConsole("Pixel_9") as console:
        yield Rig(tmp_path, server, console)


def test_what_an_android_device_runs_is_said_as_a_person_reads_it() -> None:
    assert runtime_label("16", "36") == "Android 16 (API 36)"
    assert runtime_label("16", "") == "Android 16" and runtime_label("", "36") == "API 36"
    assert runtime_label("", "") == "Android"


async def test_every_avd_is_listed_and_one_running_is_its_emulator(rig: Rig) -> None:
    listed = {choice["udid"]: choice for choice in await rig.backend.choices(rig.config, ())}
    assert listed[PIXEL] == {
        "udid": PIXEL, "name": "Pixel 9", "runtime": "API 36", "state": "Shutdown", "created": False,
        "kind": "android", "connection": None, "detail": None, "usable": True,
    }  # fmt: skip
    broken = listed[android_id("avd", "Broken")]
    assert not broken["usable"] and str(broken["detail"]).startswith("its system image is not installed")
    rig.emulator_up()
    running = await rig.backend.lookup(PIXEL, rig.config)
    assert running is not None and (running["state"], running["runtime"]) == ("Booted", "Android 16 (API 36)")
    assert await rig.backend.lookup("avd-Nothing-000000", rig.config) is None


async def test_an_emulator_of_an_avd_kept_elsewhere_is_listed_by_its_name(rig: Rig) -> None:
    rig.emulator_up(avd="Elsewhere_AVD")
    listed = await rig.services.directory.devices(rig.sdk, phones=False)
    elsewhere = next(device for device in listed if device.id == android_id("avd", "Elsewhere_AVD"))
    assert (elsewhere.name, elsewhere.running, elsewhere.avd) == ("Elsewhere AVD", True, None)


async def test_phones_are_listed_by_serial_number_and_one_not_allowed_yet_says_what_to_do(rig: Rig) -> None:
    rig.phone()
    rig.server.device("192.168.1.9:5555", "unauthorized", transport_id="3")
    rig.server.device("R58M99XYZ", "offline", transport_id="4")
    rig.server.device("R58M77QQQ", "sideloading", transport_id="5")
    listed = {choice["udid"]: choice for choice in await rig.backend.choices(rig.config, ())}
    phone = listed[PHONE]
    assert (phone["name"], phone["runtime"], phone["state"], phone["connection"], phone["usable"]) == (
        "Pixel 8", "Android 15 (API 35)", "Connected", "usb", True,
    )  # fmt: skip
    waiting = listed[android_id("adb", "192.168.1.9:5555")]
    assert (waiting["state"], waiting["connection"], waiting["usable"]) == ("Unauthorized", "network", False)
    assert waiting["detail"] == "allow USB debugging on the phone, then try again"
    assert (
        listed[android_id("adb", "R58M99XYZ")]["detail"] == "it cannot be reached now: unplug it and plug it in again"
    )
    assert listed[android_id("adb", "R58M77QQQ")]["detail"] == "it is sideloading"
    without_phones = await rig.backend.choices(rig.config.with_values(android_phones=False), ())
    assert {choice["udid"] for choice in without_phones} == {PIXEL, android_id("avd", "Broken")}
    assert await rig.backend.choices(rig.config.with_values(android=False), ()) == []
    assert await rig.backend.lookup(PIXEL, rig.config.with_values(android=False)) is None


async def test_what_a_device_says_of_itself_is_asked_once_a_connection(rig: Rig) -> None:
    rig.phone()
    await rig.backend.choices(rig.config, ())
    await rig.backend.choices(rig.config, ())
    assert sum(command.startswith("getprop ro.serialno") for command in rig.server.commands()) == 1
    rig.services.directory.forget("R58M12ABC")
    await rig.backend.choices(rig.config, ())
    assert sum(command.startswith("getprop ro.serialno") for command in rig.server.commands()) == 2


async def test_a_ready_phone_that_does_not_answer_is_listed_but_cannot_be_picked(rig: Rig) -> None:
    rig.server.device("R58M12ABC", transport_id="2")
    rig.server.on("getprop ro.serialno", code=1, err="error: closed\n")
    listed = await rig.backend.choices(rig.config, ())
    phone = next(choice for choice in listed if choice["connection"] == "usb")
    assert phone["detail"] == "it does not answer adb now" and not phone["usable"]


async def test_a_scope_gets_its_remembered_device_else_a_running_emulator_else_one_that_can_start(rig: Rig) -> None:
    assert await rig.backend.resolve(SCOPE, None, rig.config) == DeviceRef(
        PIXEL, "Pixel 9", "API 36", False, "android", None
    )
    rig.phone()
    assert (await rig.backend.resolve(SCOPE, PHONE, rig.config)).connection == "usb"
    with pytest.raises(NoDevice, match="That Android emulator or phone is not on this Mac"):
        await rig.backend.resolve(SCOPE, "avd-Gone-000000", rig.config)
    with pytest.raises(NoDevice, match="Broken: its system image is not installed"):
        await rig.backend.resolve(SCOPE, android_id("avd", "Broken"), rig.config)
    with pytest.raises(NoDevice, match="Android devices are not available here"):
        await rig.backend.resolve(SCOPE, None, rig.config.with_values(android=False))
    for ini in rig.avds.glob("*.ini"):
        ini.unlink()
    with pytest.raises(NoDevice, match="No Android emulator here can start"):
        await rig.backend.resolve(SCOPE, None, rig.config)


async def test_an_avd_not_running_is_started_headless_and_waited_for_until_it_has_booted(rig: Rig) -> None:
    rig.server.on("getprop ro.boot.qemu.avd_name", out="Pixel_9\n16\n36\n")
    boots = iter(["0\n", "1\n"])
    rig.server.on("getprop sys.boot_completed", answer=lambda command, data: (0, next(boots, "1\n").encode(), b""))
    instance = rig.instance()
    await rig.backend.prepare(instance, rig.config)
    command = rig.spawned.calls[0]
    assert command[:5] == [str(rig.sdk.emulator), "-avd", "Pixel_9", "-port", "5554"] and "-no-window" in command
    assert instance.booted_by_us and instance.runtime == "Android 16 (API 36)" and instance.connection is None
    assert rig.slept == [1.0]
    assert "pm path android" in rig.server.commands() and "wm dismiss-keyguard" in rig.server.commands()


async def test_an_emulator_that_does_not_boot_in_time_says_where_to_look(rig: Rig) -> None:
    rig.server.on("getprop sys.boot_completed", out="0\n")
    with pytest.raises(EmulatorError, match="did not boot within 30s"):
        await rig.backend.prepare(rig.instance(), rig.config.with_values(android_boot_timeout=30))


async def test_an_emulator_cannot_start_without_an_sdk(rig: Rig) -> None:
    rig.services.sdk_path = str(rig.sdk.root / "gone")
    rig.services.directory = AndroidDirectory(rig.services.adb, avds_of=lambda sdk: rig._avds(rig.sdk))
    with pytest.raises(NoDevice, match="No Android SDK was found"):
        await rig.backend.prepare(rig.instance(), rig.config.with_values(android_sdk_path=str(rig.sdk.root / "gone")))


async def test_an_emulator_already_running_is_adopted_and_known_as_this_hosts_only_if_it_started_it(rig: Rig) -> None:
    rig.emulator_up()
    instance = rig.instance()
    await rig.backend.prepare(instance, rig.config)
    assert not instance.booted_by_us and rig.spawned.calls == []
    record = rig.services.emulators
    rig.running[555] = "emulator -avd Pixel_9 -port 5554"
    (rig.services.emulators._folder).mkdir(parents=True, exist_ok=True)
    (record._folder / "Pixel_9.json").write_text('{"pid": 555, "owner": "host-a", "avd": "Pixel_9", "port": 5554}')
    again = rig.instance()
    await rig.backend.prepare(again, rig.config)
    assert again.booted_by_us


async def test_a_phone_is_used_while_it_is_connected_and_never_started(rig: Rig) -> None:
    rig.phone()
    instance = rig.instance(PHONE, "usb")
    await rig.backend.prepare(instance, rig.config)
    assert instance.connection == "usb" and rig.spawned.calls == []
    rig.server.device("R58M12ABC", "offline", transport_id="2")
    with pytest.raises(NoDevice, match="R58M12ABC is not connected"):
        await rig.backend.prepare(rig.instance(PHONE, "usb"), rig.config)
    with pytest.raises(NoDevice, match="is not on this Mac"):
        await rig.backend.prepare(rig.instance("adb-Nothing-000000"), rig.config)


async def test_an_emulator_is_shut_down_through_its_console_only_when_asked_and_a_phone_never(rig: Rig) -> None:
    rig.emulator_up()
    await rig.backend.release(rig.instance(), shutdown=False)
    assert rig.console.commands == []
    rig.services.emulators._folder.mkdir(parents=True, exist_ok=True)
    (rig.services.emulators._folder / "Pixel_9.json").write_text("{}")
    await rig.backend.release(rig.instance(), shutdown=True)
    assert rig.console.killed and not (rig.services.emulators._folder / "Pixel_9.json").exists()
    rig.phone()
    await rig.backend.release(rig.instance(PHONE, "usb"), shutdown=True)
    assert rig.console.commands == ["kill"]
    rig.server.unplug("emulator-5554")
    await rig.backend.release(rig.instance(), shutdown=True)
    assert rig.console.commands == ["kill"], "an emulator no longer running has nothing to shut down"


async def test_letting_a_device_go_ends_its_route(rig: Rig) -> None:
    import asyncio

    task = asyncio.get_running_loop().create_task(asyncio.sleep(10))
    rig.services.routes[PIXEL] = task
    await rig.backend.release(rig.instance(), shutdown=False)
    await asyncio.sleep(0)
    assert task.cancelled() and PIXEL not in rig.services.routes


async def test_a_device_closed_or_unplugged_outside_simmirror_is_gone(rig: Rig) -> None:
    rig.emulator_up()
    rig.phone()
    pixel, phone = rig.instance(), rig.instance(PHONE, "usb")
    assert await rig.backend.gone([pixel, phone]) == []
    # adb drops a connection and makes it again: listed offline, or not at all, while its emulator still runs.
    rig.server.device("emulator-5554", "offline").device("R58M12ABC", "offline")
    assert await rig.backend.gone([pixel, phone]) == []
    rig.server.unplug("emulator-5554")
    assert await rig.backend.gone([pixel, phone]) == []
    rig.console_gone = True
    rig.server.unplug("R58M12ABC")
    assert await rig.backend.gone([pixel, phone]) == [pixel, phone]
    # A device never seen running here is not one to wait for.
    assert await rig.backend.gone([rig.instance(android_id("avd", "Other"))]) != []


async def test_a_device_cannot_be_called_gone_while_adb_does_not_answer(tmp_path: Path) -> None:
    services = AndroidServices.build(_State(tmp_path))  # type: ignore[arg-type]
    services.adb.address = str(tmp_path / "no-adb.sock")
    assert await AndroidBackend(services).gone([]) == []


async def test_what_runs_here_is_an_emulator_and_what_settings_withhold(rig: Rig) -> None:
    emulator, phone = rig.instance(), rig.instance(PHONE, "usb")
    assert rig.backend.runs_here(emulator) and not rig.backend.runs_here(phone)
    assert rig.backend.withheld(emulator, rig.config) is None
    assert rig.backend.withheld(phone, rig.config.with_values(android_phones=False)) is not None
    assert rig.backend.withheld(emulator, rig.config.with_values(android_phones=False)) is None
    assert rig.backend.withheld(emulator, rig.config.with_values(android=False)) is not None
    assert rig.backend.developer_dir(rig.config) == str(rig.sdk.root)
    assert rig.backend.developer_dir(rig.config.with_values(android_sdk_path="/nowhere")) == ""
    assert rig.backend.control("").adb is rig.services.adb


async def test_the_services_start_adbs_server_from_the_sdk_named(tmp_path: Path) -> None:
    sdk = make_sdk(tmp_path / "sdk")
    services = AndroidServices.build(_State(tmp_path))  # type: ignore[arg-type]
    assert services.sdk(str(sdk.root)) == sdk and services.sdk() == sdk
    assert services.adb._adb_path() == sdk.adb
    services.sdk_path = ""
    assert services.sdk() is None and services.adb._adb_path() is None


async def test_an_emulator_that_comes_up_as_another_avd_or_never_comes_up_is_not_taken_for_this_one(rig: Rig) -> None:
    rig.server.on("getprop ro.boot.qemu.avd_name", out="Other_AVD\n16\n36\n").on(
        "getprop sys.boot_completed", out="1\n"
    )
    with pytest.raises(EmulatorError, match="did not boot within 30s"):
        await rig.backend.prepare(rig.instance(), rig.config.with_values(android_boot_timeout=30))
    rig.spawned.server = FakeAdbServer()  # adb never lists the emulator started
    rig.server.unplug("emulator-5554")
    with pytest.raises(EmulatorError, match="did not boot"):
        await rig.backend.prepare(rig.instance(), rig.config.with_values(android_boot_timeout=30))


async def test_an_emulator_that_does_not_say_which_avd_it_is_is_not_listed_as_one(rig: Rig) -> None:
    rig.server.device("emulator-5554", transport_id="1")
    rig.server.on("getprop ro.boot.qemu.avd_name", out="\n\n\n")
    listed = await rig.services.directory.devices(rig.sdk, phones=False)
    assert all(not device.running for device in listed)


async def test_an_emulator_of_an_avd_kept_elsewhere_is_adopted_and_let_go(rig: Rig) -> None:
    rig.emulator_up(avd="Elsewhere_AVD")
    elsewhere = rig.instance(android_id("avd", "Elsewhere_AVD"))
    await rig.backend.prepare(elsewhere, rig.config)
    assert not elsewhere.booted_by_us
    await rig.backend.release(elsewhere, shutdown=True)
    assert rig.console.killed


async def test_a_serial_is_only_had_for_a_running_device(rig: Rig) -> None:
    with pytest.raises(AdbError, match="Pixel 9 is not running"):
        await rig.services.directory.serial(PIXEL)
    with pytest.raises(AdbError, match="avd-Gone-000000 is not running"):
        await rig.services.directory.serial("avd-Gone-000000")


def test_a_device_as_a_picker_lists_it() -> None:
    device = AndroidDevice(PHONE, "Pixel 8", "phone", "R58M12ABC", "unauthorized", "Android", "usb", "allow it")
    assert choice_of(device)["state"] == "Unauthorized" and not choice_of(device)["usable"]


class _State:
    """The part of a state store the Android services ask for."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.owner_tag = "host-a"

    def run_dir(self) -> Path:
        return self.root / "run"

    def log_dir(self) -> Path:
        return self.root / "logs"
