# SPDX-License-Identifier: Apache-2.0
"""The iphone connector: a real device's screen by screenshot, and what it is offered by what it says it can do."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, Crop
from sim_mirror.connectors.iphone.connector import ALWAYS, MOST, IPhoneConnector, capabilities_of, create
from sim_mirror.connectors.iphone.screen import FPS_LIMIT, DevicectlScreen, screen_of
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display
from sim_mirror.testing.fakes import PHONE_UDID, FakeXcrun, MemoryStateStore, tiny_jpeg

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


async def test_the_connector_reaches_a_connected_device_by_screenshot_and_says_what_would_do_more() -> None:
    fake = FakeXcrun().with_devicectl()
    context = ConnectorContext(state=MemoryStateStore(Path("/nowhere")), copy=HostCopy(), simctl_for=None, xcrun=fake)  # type: ignore[arg-type]
    connector = create(context)
    assert connector.name == "iphone" and connector.kinds == frozenset({"physical"})
    report = await connector.probe(ON)
    assert report.available and report.capabilities == MOST and report.kinds == frozenset({"physical"})
    session = await connector.attach(PHONE_UDID, ON)
    assert session.connector == "iphone" and session.fps_limit == 1 and session.input is None
    assert session.note is not None and "sim-mirror wda setup" in session.note and "cable" in session.note
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
