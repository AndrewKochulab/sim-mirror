# SPDX-License-Identifier: Apache-2.0
"""The iphone connector: a real device's screen by screenshot, and what it is offered by what it says it can do."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, Crop
from sim_mirror.connectors.iphone.cable import UsbCable
from sim_mirror.connectors.iphone.capture import CableCapture, LiveScreen
from sim_mirror.connectors.iphone.connector import (
    ALWAYS,
    DRIVEN,
    LIVE,
    MOST,
    WDA_FPS_LIMIT,
    IPhoneConnector,
    capabilities_of,
    create,
)
from sim_mirror.connectors.iphone.screen import FPS_LIMIT, DevicectlScreen, screen_of
from sim_mirror.connectors.iphone.wda_client import WdaClient
from sim_mirror.connectors.iphone.wda_roles import WdaInput, WdaText
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.core.device_logs import DeviceLogBook
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display, PhysicalDevice
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.lockdown import SYSLOG_RELAY
from sim_mirror.platform.usbmux import Usbmux, UsbmuxError
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import PHONE_UDID, FakeEngine, FakeXcrun, MemoryStateStore, fixture_json, tiny_jpeg
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.usbmux import FakeMuxd, FakeService
from sim_mirror.testing.wda import FakeWda

ON = SimConfig.defaults().with_values(real_devices=True, developer_dir="/X.app/Contents/Developer")
DISPLAY = Display(1179, 2556, 3.0, "portrait")


class Shots:
    """devicectl's screenshots: a PNG written where asked, or a failure."""

    def __init__(self, png: bytes = b"\x89PNG fake", fail: Exception | None = None) -> None:
        self.png = png
        self.fail = fail

    async def screenshot(self, udid: str, destination: Path) -> None:
        if self.fail is not None:
            raise self.fail
        if self.png:
            destination.write_bytes(self.png)


def converter(answer: bytes | None) -> Any:
    seen: list[tuple[bytes, int | None, int]] = []

    async def convert(data: bytes, *, max_width: int | None, quality: int) -> bytes | None:
        seen.append((data, max_width, quality))
        return answer

    convert.seen = seen  # type: ignore[attr-defined]
    return convert


async def test_a_screenshot_is_made_a_jpeg_no_wider_than_asked_and_the_screen_is_measured_exactly() -> None:
    jpeg = tiny_jpeg(400, 868)
    convert = converter(jpeg)
    screen = DevicectlScreen(Shots(), PHONE_UDID, DISPLAY, convert=convert)  # type: ignore[arg-type]
    described = await screen.describe()
    assert (described.width_pt, described.height_pt, described.scale) == (393, 852, 3.0)
    shot = await screen.screenshot(max_width=400, quality=70)
    assert (shot.width, shot.height, shot.jpeg) == (400, 868, jpeg)
    await screen.screenshot(max_width=2000, quality=80)
    assert convert.seen == [(b"\x89PNG fake", 400, 70), (b"\x89PNG fake", None, 80)]
    assert screen_of(Display(750, 1334, 2.0, "portrait")).width_pt == 375 and FPS_LIMIT == 1


@pytest.mark.parametrize(
    ("shots", "answer", "said"),
    [
        (Shots(fail=DevicectlError("the device is locked")), b"", "taking a screenshot failed: the device is locked"),
        (Shots(png=b""), b"", "devicectl wrote no picture"),
        (Shots(), None, "could not be made a JPEG"),
        (Shots(), b"not a jpeg", "could not be made a JPEG"),
    ],
)
async def test_a_screenshot_that_cannot_be_had_says_why(shots: Shots, answer: bytes | None, said: str) -> None:
    screen = DevicectlScreen(shots, PHONE_UDID, DISPLAY, convert=converter(answer))  # type: ignore[arg-type]
    with pytest.raises(ConnectorError, match=said):
        await screen.screenshot(max_width=400, quality=70)


async def test_a_region_or_a_live_stream_needs_more_than_screenshots() -> None:
    screen = DevicectlScreen(Shots(), PHONE_UDID, DISPLAY, convert=converter(None))  # type: ignore[arg-type]
    with pytest.raises(ConnectorError, match="needs the device's cable or WebDriverAgent"):
        await screen.screenshot(max_width=400, quality=70, crop=Crop(0, 0, 10, 10))
    with pytest.raises(ConnectorError, match="needs its cable"):
        async for _unit in screen.h264(fps=30, scale=1.0, key_frame_s=1.0, bitrate=1_000_000):
            pass


async def test_a_device_is_offered_what_it_says_it_can_do() -> None:
    [_second, phone] = await Devicectl(FakeXcrun().with_devicectl()).devices()
    offered = capabilities_of(phone)
    assert offered >= ALWAYS and {Capability.SCREENSHOT, Capability.APP_LAUNCH, Capability.LOCATION} <= offered
    assert Capability.STATUS_BAR not in offered and Capability.INPUT_TOUCH not in offered
    bare = dataclasses.replace(phone, features=frozenset({"simulateStatusBar"}))
    assert capabilities_of(bare) == ALWAYS | {Capability.STATUS_BAR}


async def test_the_connector_reaches_a_device_by_screenshot_and_says_what_would_do_more(tmp_path: Path) -> None:
    fake = FakeXcrun().with_devicectl()
    context = ConnectorContext(state=MemoryStateStore(tmp_path), copy=HostCopy(), simctl_for=None, xcrun=fake)  # type: ignore[arg-type]
    connector = create(context)
    assert connector.name == "iphone" and connector.kinds == frozenset({"physical"})
    report = await connector.probe(ON)
    assert report.available and report.capabilities == MOST and report.kinds == frozenset({"physical"})
    session = await connector.attach(PHONE_UDID, ON)
    assert session.connector == "iphone" and session.fps_limit == 1 and session.input is None
    assert session.note is not None and "plug the device in by cable for a live picture" in session.note
    assert "WebDriverAgent is reached only over the device's cable" in session.note, "on by default, and uncabled"
    watched = await connector.attach(PHONE_UDID, ON.with_values(wda_enabled=False))
    assert watched.note is not None and "needs WebDriverAgent, which is off here" in watched.note
    assert (await session.screen.describe()).width_px == 1179
    assert all(call.developer_dir == "/X.app/Contents/Developer" for call in fake.calls)
    own = ON.with_values(real_devices_developer_dir="/Y.app/Contents/Developer")
    await connector.attach(PHONE_UDID, own)
    assert fake.calls[-1].developer_dir == "/Y.app/Contents/Developer"
    assert await connector.reap_orphans() == 0


async def test_the_connector_refuses_what_it_cannot_reach() -> None:
    connector = IPhoneConnector(lambda xcode: Devicectl(FakeXcrun().with_devicectl()), has_xcrun=lambda: False)
    off = await connector.probe(SimConfig.defaults().with_values(real_devices=False))
    assert not off.available and "Real devices are not available here" in off.reasons[0]
    assert (await connector.probe(ON)).reasons == ("Xcode command-line tools are not installed (no xcrun).",)
    with pytest.raises(ConnectorUnavailable, match="00008120-0000000000000000 is not connected") as missing:
        await connector.attach("00008120-0000000000000000", ON)
    assert missing.value.status == 409
    failing = IPhoneConnector(
        lambda xcode: Devicectl(FakeXcrun().on("devicectl", rc=1, err="CoreDevice is not running"))
    )
    with pytest.raises(ConnectorUnavailable, match="CoreDevice is not running"):
        await failing.attach(PHONE_UDID, ON)


async def test_a_device_whose_tunnel_was_idle_is_offered_all_it_can_do_once_asked_something() -> None:
    listing = fixture_json("devicectl-devices.json")
    idle = json.loads(json.dumps(listing))
    for device in idle["result"]["devices"]:
        device["capabilities"] = device["capabilities"][:5]
    answers = [json.dumps(idle), json.dumps(listing)]

    def listed(args: tuple[str, ...]) -> XcrunResult:
        return XcrunResult(0, answers.pop(0) if len(answers) > 1 else answers[0], "")

    fake = FakeXcrun().with_devicectl().on("devicectl", "-q", "list", "devices", then=listed)
    session = await IPhoneConnector(lambda xcode: Devicectl(fake)).attach(PHONE_UDID, ON)
    assert {Capability.APP_LAUNCH, Capability.SCREENSHOT} <= session.capabilities
    assert [call.args[2:5] for call in fake.calls] == [
        ("list", "devices", "--json-output"),
        ("device", "info", "displays"),
        ("list", "devices", "--json-output"),
    ]


class Cable:
    """A device's cable as a test says: plugged in or not, and its log readable or not."""

    def __init__(self, *, plugged: bool = True, log: Exception | None = None) -> None:
        self.plugged = plugged
        self.log = log
        self.opened: list[str] = []

    def cabled(self, udid: str) -> bool:
        return self.plugged

    def open_log(self, udid: str) -> Any:
        self.opened.append(udid)
        if self.log is not None:
            raise self.log
        return Stream()


class Stream:
    def __init__(self) -> None:
        self.closed = False

    def recv(self, size: int) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


async def test_a_cabled_devices_log_is_kept_while_it_is_driven_and_one_without_a_cable_says_so() -> None:
    book = DeviceLogBook()
    cable = Cable()
    connector = IPhoneConnector(lambda xcode: Devicectl(FakeXcrun().with_devicectl()), cable=cable, logs=book)
    session = await connector.attach(PHONE_UDID, ON.with_values(real_devices_log_buffer_mb=8))
    assert Capability.LOGS in session.capabilities and cable.opened == [PHONE_UDID]
    assert book.lines(PHONE_UDID, since_s=60, bundle_id=None) == []
    await session.close()
    assert book.lines(PHONE_UDID, since_s=60, bundle_id=None) is None
    cable.plugged = False
    unplugged = await connector.attach(PHONE_UDID, ON)
    assert Capability.LOGS not in unplugged.capabilities and cable.opened == [PHONE_UDID]
    unreadable = IPhoneConnector(
        lambda xcode: Devicectl(FakeXcrun().with_devicectl()),
        cable=Cable(log=DeviceControlError("no pairing record")),
        logs=book,
    )
    assert Capability.LOGS not in (await unreadable.attach(PHONE_UDID, ON)).capabilities
    logless = IPhoneConnector(lambda xcode: Devicectl(FakeXcrun().with_devicectl()), cable=Cable())
    no_book = await logless.attach(PHONE_UDID, ON)
    assert Capability.LOGS not in no_book.capabilities
    await no_book.close()


def test_the_real_cable_asks_usbmuxd() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.lockdownd().services[SYSLOG_RELAY] = (50324, False)
    muxd.ports[50324] = FakeService(b"log")
    cable = UsbCable(Usbmux("/tmp/fake-usbmuxd", connect=muxd.connect))
    assert cable.cabled(PHONE_UDID) and not cable.cabled("00008150-0099887766554433")
    with pytest.raises(DeviceControlError, match="the pairing record's certificate cannot be used"):
        cable.open_log(PHONE_UDID)
    broken = UsbCable(Usbmux("/tmp/nowhere", connect=lambda path, timeout: (_ for _ in ()).throw(UsbmuxError("gone"))))
    assert not broken.cabled(PHONE_UDID)
    assert isinstance(UsbCable().usbmux, Usbmux)


def test_a_host_s_connector_has_a_cable_its_helpers_and_somewhere_to_keep_logs_when_it_has() -> None:
    state = MemoryStateStore(Path("/nowhere"))
    kept = create(ConnectorContext(state=state, copy=HostCopy(), simctl_for=None, device_logs=DeviceLogBook()))  # type: ignore[arg-type]
    plain = create(ConnectorContext(state=state, copy=HostCopy(), simctl_for=None))  # type: ignore[arg-type]
    assert isinstance(kept._cable, UsbCable) and isinstance(kept._screens, CableCapture)
    assert kept._logs is not None and plain._logs is None


class Screens:
    """What shows a cabled device's live screen, as a test says: a helper's screen, or why there is none."""

    def __init__(self, fail: str | None = None, reaped: int = 0) -> None:
        self.fail = fail
        self.reaped = reaped
        self.opened: list[tuple[str, bool]] = []
        self.closed = 0
        self.running = True

    async def open(
        self, device: PhysicalDevice, display: Display, config: SimConfig, devicectl: Devicectl, *, twins: bool
    ) -> LiveScreen:
        self.opened.append((device.udid, twins))
        if self.fail is not None:
            raise ConnectorUnavailable(self.fail, 409)

        async def close() -> None:
            self.closed += 1

        return LiveScreen(FakeEngine(), lambda: self.running, close)

    async def reap_orphans(self) -> int:
        return self.reaped


def cabled(screens: Screens, *, plugged: bool = True, fake: FakeXcrun | None = None) -> IPhoneConnector:
    return IPhoneConnector(
        lambda xcode: Devicectl(fake or FakeXcrun().with_devicectl()), cable=Cable(plugged=plugged), screens=screens
    )


async def test_a_cabled_device_shows_its_live_screen_and_is_offered_a_stream() -> None:
    screens = Screens(reaped=2)
    session = await cabled(screens).attach(PHONE_UDID, ON)
    assert screens.opened == [(PHONE_UDID, False)]
    assert session.capabilities >= LIVE and session.fps_limit is None and session.is_alive is not None
    assert session.note == HostCopy().iphone_limits(live=True) and "WebDriverAgent" in session.note
    assert isinstance(session.screen, FakeEngine) and session.is_alive()
    await session.close()
    assert screens.closed == 1
    assert await cabled(screens).reap_orphans() == 2
    assert await IPhoneConnector(lambda xcode: Devicectl(FakeXcrun())).reap_orphans() == 0


async def test_a_cable_that_cannot_show_the_screen_leaves_screenshots_and_says_why() -> None:
    session = await cabled(Screens(fail="macOS has not let sim-mirror-helper use the Camera")).attach(PHONE_UDID, ON)
    assert isinstance(session.screen, DevicectlScreen) and session.fps_limit == FPS_LIMIT
    assert Capability.STREAM_H264 not in session.capabilities
    assert session.note is not None and session.note.startswith(
        "Its cable could not show the screen (macOS has not let sim-mirror-helper use the Camera), so it shows"
    )
    unplugged = Screens()
    session = await cabled(unplugged, plugged=False).attach(PHONE_UDID, ON)
    assert unplugged.opened == [] and session.note == HostCopy().iphone_limits()
    by_screenshot = Screens()
    await cabled(by_screenshot).attach(PHONE_UDID, ON.with_values(real_devices_screen="screenshot"))
    assert by_screenshot.opened == []


async def test_a_screen_read_only_over_the_cable_refuses_a_device_it_cannot_show() -> None:
    usb = ON.with_values(real_devices_screen="usb")
    with pytest.raises(ConnectorUnavailable, match="Test iPhone's screen is read only over its cable") as missing:
        await cabled(Screens(), plugged=False).attach(PHONE_UDID, usb)
    assert missing.value.status == 409
    with pytest.raises(ConnectorUnavailable, match="use the Camera"):
        await cabled(Screens(fail="use the Camera")).attach(PHONE_UDID, usb)
    assert (await cabled(Screens()).attach(PHONE_UDID, usb)).fps_limit is None


async def test_a_device_sharing_its_name_with_another_cabled_one_is_told_apart() -> None:
    twins = json.loads(json.dumps(fixture_json("devicectl-devices.json")))
    for device in twins["result"]["devices"]:
        if device["hardwareProperties"].get("udid") != PHONE_UDID:
            # devicectl says each twice: in its deprecated keys, and in the properties read first.
            device["deviceProperties"]["name"] = device["properties"]["state"]["name"] = "Test iPhone"
            device["connectionProperties"]["transportType"] = "wired"
            device["properties"]["connection"]["transportType"] = "wired"
    fake = FakeXcrun().with_devicectl().on("devicectl", "-q", "list", "devices", out=json.dumps(twins))
    screens = Screens()
    await cabled(screens, fake=fake).attach(PHONE_UDID, ON)
    assert screens.opened == [(PHONE_UDID, True)]
    lone = Screens()
    await cabled(lone).attach(PHONE_UDID, ON)
    assert lone.opened == [(PHONE_UDID, False)], "the other device has another name and no cable"


class Driver:
    """WebDriverAgent's service as a test says: a client for the device, or why there is none."""

    def __init__(self, wda: FakeWda | None = None, fail: str | None = None) -> None:
        self.wda = wda
        self.fail = fail
        self.asked: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.running = True

    async def client(self, udid: str, config: SimConfig, developer_dir: str) -> WdaClient:
        self.asked.append((udid, developer_dir))
        if self.fail is not None or self.wda is None:
            raise ConnectorUnavailable(self.fail or "no WebDriverAgent", 409)
        return WdaClient(self.wda.opener())

    async def stop(self, udid: str) -> None:
        self.stopped.append(udid)

    def alive(self, udid: str) -> bool:
        return self.running

    async def reap_orphans(self) -> int:
        return 3


def driven(driver: Driver, screens: Screens | None = None, *, plugged: bool = True) -> IPhoneConnector:
    return IPhoneConnector(
        lambda xcode: Devicectl(FakeXcrun().with_devicectl()),
        cable=Cable(plugged=plugged),
        screens=screens,
        wda=driver,  # type: ignore[arg-type]
    )


WDA_ON = ON.with_values(wda_enabled=True)


async def test_a_cabled_device_webdriveragent_drives_is_touched_typed_on_and_read(tmp_path: Path) -> None:
    with short_run_dir() as folder:
        wda = await FakeWda(folder).serve()
        try:
            driver, screens = Driver(wda), Screens()
            session = await driven(driver, screens).attach(PHONE_UDID, WDA_ON)
            assert session.capabilities >= DRIVEN and session.capabilities >= LIVE and session.note is None
            assert isinstance(session.input, WdaInput) and isinstance(session.text, WdaText)
            assert session.reader is not None and (await session.reader.accessibility())["backend"] == "wda"
            assert driver.asked == [(PHONE_UDID, "/X.app/Contents/Developer")] and session.is_alive()
            driver.running = False
            assert not session.is_alive(), "a WebDriverAgent that ended ends the session"
            await session.close()
            assert driver.stopped == [PHONE_UDID] and screens.closed == 1
            kept = await driven(driver).attach(PHONE_UDID, WDA_ON.with_values(wda_keep_running=True))
            await kept.close()
            assert driver.stopped == [PHONE_UDID], "kept running when the settings say so"
            assert await driven(driver, Screens(reaped=2)).reap_orphans() == 5
        finally:
            await wda.stop()


async def test_without_a_cable_screen_webdriveragents_screenshots_are_shown(tmp_path: Path) -> None:
    with short_run_dir() as folder:
        wda = await FakeWda(folder).serve()
        try:
            session = await driven(Driver(wda)).attach(PHONE_UDID, WDA_ON)
            assert isinstance(session.screen, DevicectlScreen) and session.fps_limit == WDA_FPS_LIMIT
            assert session.note == HostCopy().iphone_limits(touch=True)
            assert Capability.STREAM_H264 not in session.capabilities
            only = await driven(Driver(wda), Screens()).attach(PHONE_UDID, ON.with_values(real_devices_screen="wda"))
            assert session.fps_limit == WDA_FPS_LIMIT and only.input is not None, "the wda screen needs WebDriverAgent"
        finally:
            await wda.stop()


async def test_a_device_webdriveragent_cannot_drive_is_watched_and_says_why() -> None:
    session = await driven(Driver(fail="the developer is not trusted")).attach(PHONE_UDID, WDA_ON)
    assert session.input is None and session.text is None and not DRIVEN & session.capabilities
    assert session.note is not None
    assert session.note.endswith("cannot be touched or read yet: the developer is not trusted")
    unplugged = await driven(Driver(), plugged=False).attach(PHONE_UDID, WDA_ON)
    assert unplugged.note is not None and "reached only over the device's cable" in unplugged.note
    off = Driver()
    await driven(off).attach(PHONE_UDID, ON.with_values(wda_enabled=False))
    assert off.asked == [], "not asked for while it is off"
    with pytest.raises(ConnectorUnavailable, match="not trusted"):
        await driven(Driver(fail="not trusted")).attach(PHONE_UDID, ON.with_values(real_devices_screen="wda"))


async def test_a_live_screen_is_let_go_when_webdriveragent_is_let_go_of_mid_start() -> None:
    class Cancelled(Driver):
        async def client(self, udid: str, config: SimConfig, developer_dir: str) -> WdaClient:
            raise asyncio.CancelledError

    screens = Screens()
    with pytest.raises(asyncio.CancelledError):
        await driven(Cancelled(), screens).attach(PHONE_UDID, WDA_ON)
    assert screens.closed == 1
