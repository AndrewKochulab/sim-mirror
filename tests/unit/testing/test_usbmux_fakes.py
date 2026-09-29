# SPDX-License-Identifier: Apache-2.0
"""The fake usbmuxd and lockdownd answer what the real ones would, including what they do not understand."""

from __future__ import annotations

import plistlib
import struct

from sim_mirror.platform.usbmux import HEADER, Usbmux, encode
from sim_mirror.testing.fakes import PHONE_UDID
from sim_mirror.testing.usbmux import FakeMuxd, FakeService


def test_a_service_only_sends_and_usbmuxd_refuses_what_it_does_not_know() -> None:
    service = FakeService(b"hi")
    assert service.heard(b"anything") == b"" and service.first() == b"hi"
    usbmux = Usbmux("/tmp/fake", connect=FakeMuxd().connect)
    assert usbmux.request({"MessageType": "Listen"}) == {"MessageType": "Result", "Number": 1}


def test_lockdownd_answers_whole_requests_in_order_and_refuses_what_it_does_not_know() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    lockdownd = muxd.lockdownd()
    assert lockdownd.first() == b""
    body = plistlib.dumps({"Request": "Pair"})
    framed = struct.pack(">I", len(body)) + body
    assert lockdownd.heard(framed[:6]) == b"", "half a request is kept until the rest comes"
    answer = lockdownd.heard(framed[6:])
    assert plistlib.loads(answer[4:]) == {"Request": "Pair", "Error": "UnknownRequest"}
    with muxd.connect("/tmp/fake", 1.0) as sock:
        sock.settimeout(3.0)
        sock.sendall(encode({"MessageType": "ListDevices"}))
        length = HEADER.unpack(sock.recv(HEADER.size))[0]
        assert plistlib.loads(sock.recv(length - HEADER.size))["DeviceList"][0]["DeviceID"] == 12
        assert sock.timeout == 3.0
    assert sock.closed
