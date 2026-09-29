# SPDX-License-Identifier: Apache-2.0
"""usbmuxd and lockdownd, spoken to as their wire formats say: the devices, a pairing record, a port, a session, a
value and a service -- and what either refuses, said as it said it."""

from __future__ import annotations

import plistlib
import socket
import ssl
import stat
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.platform.lockdown import SYSLOG_RELAY, Lockdown, LockdownError, open_syslog, tls_context
from sim_mirror.platform.usbmux import (
    HEADER,
    USBMUXD,
    USBMUXD_ENV,
    Usbmux,
    UsbmuxError,
    encode,
    same_udid,
    socket_path,
    unix_socket,
)
from sim_mirror.testing.fakes import PHONE_UDID
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.usbmux import PAIR_RECORD, FakeMuxd, FakeService, plain_tls


def mux(muxd: FakeMuxd) -> Usbmux:
    return Usbmux("/tmp/fake-usbmuxd", connect=muxd.connect)


def test_a_request_is_its_header_and_its_property_list_and_the_socket_can_be_moved() -> None:
    sent = encode({"MessageType": "ListDevices"}, tag=7)
    length, version, kind, tag = HEADER.unpack(sent[: HEADER.size])
    assert (length, version, kind, tag) == (len(sent), 1, 8, 7)
    assert plistlib.loads(sent[HEADER.size :]) == {
        "ClientVersionString": "sim-mirror",
        "ProgName": "sim-mirror",
        "MessageType": "ListDevices",
    }
    assert socket_path({}) == USBMUXD == "/var/run/usbmuxd" and socket_path({USBMUXD_ENV: "/tmp/m"}) == "/tmp/m"
    assert same_udid(PHONE_UDID, PHONE_UDID.replace("-", "").lower()) and not same_udid(PHONE_UDID, "00008120")
    assert Usbmux().path == socket_path()


def test_devices_are_listed_found_by_their_udid_and_their_pairing_record_read() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID).plug("00008150-0099887766554433", device_id=3, connection="Network")
    muxd.devices.append({"DeviceID": "not a number", "Properties": {}})
    muxd.devices.append({"DeviceID": 4})
    usbmux = mux(muxd)
    listed = usbmux.devices()
    assert [(d.device_id, d.connection, d.cabled) for d in listed] == [(12, "USB", True), (3, "Network", False)]
    found = usbmux.find(PHONE_UDID)
    assert found is not None and found.device_id == 12 and same_udid(found.udid, PHONE_UDID)
    assert usbmux.find("00008150-0099887766554433") is None
    assert usbmux.find("00008150-0099887766554433", cabled=False) is not None
    assert usbmux.pair_record(PHONE_UDID) == PAIR_RECORD
    with pytest.raises(UsbmuxError, match="no pairing record for 00008150-0000000000000000: unlock the device"):
        usbmux.pair_record("00008150-0000000000000000")
    assert all(sock.closed for sock in muxd.sockets)


def test_a_port_on_a_device_is_connected_or_refused() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.ports[8100] = FakeService(b"hello")
    usbmux = mux(muxd)
    sock = usbmux.connect(12, 8100)
    assert sock.recv(5) == b"hello"
    with pytest.raises(UsbmuxError, match="the device's port 9100 did not answer \\(usbmuxd said 3\\)"):
        usbmux.connect(12, 9100)
    assert muxd.sockets[-1].closed
    with pytest.raises(UsbmuxError, match="not a port: 70000"):
        usbmux.connect(12, 70000)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (HEADER.pack(8, 1, 8, 1), "answered with a length of 8"),
        (HEADER.pack(HEADER.size + 4, 1, 8, 1) + b"nope", "not a property list"),
        (HEADER.pack(HEADER.size + len(plistlib.dumps([1])), 1, 8, 1) + plistlib.dumps([1]), "not a dictionary"),
        (HEADER.pack(40, 1, 8, 1) + b"short", "closed before its answer was whole"),
    ],
)
def test_what_usbmuxd_answers_badly_is_refused(answer: bytes, said: str) -> None:
    muxd = FakeMuxd(answer_with=lambda request: answer)
    with pytest.raises(UsbmuxError, match=said):
        mux(muxd).devices()


def test_a_pairing_record_that_is_not_a_dictionary_is_refused() -> None:
    muxd = FakeMuxd(answer_with=lambda request: {"PairRecordData": plistlib.dumps([1])})
    with pytest.raises(UsbmuxError, match="is not a dictionary"):
        mux(muxd).pair_record(PHONE_UDID)


def test_usbmuxd_that_is_not_there_is_said() -> None:
    with short_run_dir() as folder:
        with pytest.raises(UsbmuxError, match=f"usbmuxd cannot be reached at {folder / 'none'}"):
            unix_socket(str(folder / "none"), 0.1)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(folder / "s"))
        server.listen(1)
        client = unix_socket(str(folder / "s"), 0.1)
        assert client.gettimeout() == 0.1
        client.close()
        server.close()


def test_a_lockdown_session_reads_values_and_starts_services_securely_when_asked() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    lockdownd = muxd.lockdownd()
    lockdownd.values["IsSupervised"] = False
    lockdownd.services[SYSLOG_RELAY] = (50324, True)
    lockdownd.services["com.apple.plain"] = (50325, False)
    muxd.ports[50324] = FakeService(b"Sep 29 00:47:18 phone backboardd[74] <Notice>: hi\n\x00")
    muxd.ports[50325] = FakeService(b"plain")
    with Lockdown(mux(muxd), PHONE_UDID, context_for=plain_tls) as session:
        assert session.value("ProductVersion") == "26.3" and session.value("IsSupervised") is False
        assert session.value("SerialNumber") is None
        assert session.value("DeviceName", domain="com.apple.x") is None
        relay = session.start_service(SYSLOG_RELAY)
        assert getattr(relay, "wrapped", False) and relay.recv(100).startswith(b"Sep 29")
        plain = session.start_service("com.apple.plain")
        assert not getattr(plain, "wrapped", False)
    assert [request["Request"] for request in lockdownd.requests] == [
        "QueryType",
        "StartSession",
        "GetValue",
        "GetValue",
        "GetValue",
        "GetValue",
        "StartService",
        "StartService",
    ]
    assert lockdownd.requests[1]["HostID"] == "HOST-ID" and lockdownd.requests[5]["Domain"] == "com.apple.x"
    with pytest.raises(LockdownError, match="the lockdown session is not open"):
        session.value("ProductVersion")


def test_a_session_lockdownd_does_not_wrap_is_spoken_in_the_clear_and_closing_twice_is_closing_once() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.lockdownd().session_ssl = False
    session = Lockdown(mux(muxd), PHONE_UDID, context_for=plain_tls).open()
    assert session.value("ProductVersion") == "26.3" and not getattr(muxd.sockets[-1], "wrapped", False)
    session.close()
    session.close()


def test_a_device_whose_lockdown_cannot_be_reached_or_refuses_says_why() -> None:
    usbmux = mux(FakeMuxd())
    with pytest.raises(LockdownError, match="is not connected by cable"):
        Lockdown(usbmux, PHONE_UDID).open()
    no_port = FakeMuxd().plug(PHONE_UDID)
    with pytest.raises(LockdownError, match="port 62078 did not answer"):
        Lockdown(mux(no_port), PHONE_UDID).open()
    impostor = FakeMuxd().plug(PHONE_UDID)
    impostor.lockdownd().kind = "com.example.other"
    with pytest.raises(LockdownError, match=r"is not lockdownd \(com\.example\.other\)"):
        Lockdown(mux(impostor), PHONE_UDID, context_for=plain_tls).open()
    refusing = FakeMuxd().plug(PHONE_UDID)
    refusing.lockdownd().refuse["StartSession"] = "InvalidHostID"
    with pytest.raises(LockdownError, match="lockdownd refused StartSession: InvalidHostID"):
        Lockdown(mux(refusing), PHONE_UDID, context_for=plain_tls).open()
    unserviced = FakeMuxd().plug(PHONE_UDID)
    unserviced.lockdownd()
    with (
        Lockdown(mux(unserviced), PHONE_UDID, context_for=plain_tls) as session,
        pytest.raises(LockdownError, match=r"started com\.apple\.nothing on no port"),
    ):
        session.start_service("com.apple.nothing")


def test_what_lockdownd_answers_badly_or_a_service_that_cannot_be_reached_is_refused() -> None:
    class Broken(FakeService):
        def heard(self, data: bytes) -> bytes:
            return self.sends

    for sent, said in (
        (b"\x00\x00\x00\x00", "a length of 0"),
        (b"\x00\x00\x00\x04nope", "not a property list"),
        (len(plistlib.dumps([1])).to_bytes(4, "big") + plistlib.dumps([1]), "not a dictionary"),
    ):
        muxd = FakeMuxd().plug(PHONE_UDID)
        muxd.ports[62078] = Broken(sent)
        with pytest.raises(LockdownError, match=said):
            Lockdown(mux(muxd), PHONE_UDID, context_for=plain_tls).open()
    gone = FakeMuxd().plug(PHONE_UDID)
    gone.lockdownd().services[SYSLOG_RELAY] = (50324, True)
    with (
        Lockdown(mux(gone), PHONE_UDID, context_for=plain_tls) as session,
        pytest.raises(LockdownError, match="port 50324 did not answer"),
    ):
        session.start_service(SYSLOG_RELAY)


def test_a_session_whose_connection_fails_mid_way_says_the_device_could_not_be_reached() -> None:
    class Failing(FakeService):
        def heard(self, data: bytes) -> bytes:
            raise OSError("broken pipe")

    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.ports[62078] = Failing()
    with pytest.raises(LockdownError, match="the device's lockdownd could not be reached: broken pipe"):
        Lockdown(mux(muxd), PHONE_UDID, context_for=plain_tls).open()


def test_a_service_that_will_not_speak_tls_is_refused() -> None:
    class Refusing:
        def wrap_socket(self, sock: Any, server_hostname: str | None = None) -> Any:
            if sock.port is not None and isinstance(sock.port, FakeService):
                raise OSError("handshake failed")
            return sock

    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.lockdownd().services[SYSLOG_RELAY] = (50324, True)
    muxd.ports[50324] = FakeService()
    refusing = Lockdown(mux(muxd), PHONE_UDID, context_for=lambda record: Refusing())  # type: ignore[arg-type, return-value]
    with refusing as session, pytest.raises(LockdownError, match="could not be reached securely: handshake failed"):
        session.start_service(SYSLOG_RELAY)


def test_the_log_relay_is_opened_through_a_session_that_then_ends() -> None:
    muxd = FakeMuxd().plug(PHONE_UDID)
    muxd.lockdownd().services[SYSLOG_RELAY] = (50324, False)
    muxd.ports[50324] = FakeService(b"log")

    relay = open_syslog(mux(muxd), PHONE_UDID, context_for=plain_tls)
    assert relay.recv(3) == b"log" and muxd.sockets[1].closed


class Context:
    """An SSL context that notes the certificate and key it is given, and what else is set on it."""

    def __init__(self, refuse: bool = False) -> None:
        self.seen: list[tuple[int, int, int]] = []
        self.refuse = refuse
        self.check_hostname = True
        self.verify_mode: Any = None
        self.ciphers = ""

    def set_ciphers(self, ciphers: str) -> None:
        self.ciphers = ciphers

    def load_cert_chain(self, certificate: Path, key: Path) -> None:
        if self.refuse:
            raise ssl.SSLError("bad key")
        modes = (certificate.stat().st_mode, key.stat().st_mode, certificate.parent.stat().st_mode)
        self.seen.append(tuple(stat.S_IMODE(mode) for mode in modes))  # type: ignore[arg-type]


def test_tls_is_made_from_the_pairing_records_certificate_kept_private_while_it_loads() -> None:
    made = Context()
    context = tls_context(PAIR_RECORD, make=lambda: made)  # type: ignore[arg-type, return-value]
    assert context is made and made.seen == [(0o600, 0o600, 0o700)]
    assert not made.check_hostname and made.verify_mode == ssl.CERT_NONE and made.ciphers == "ALL:@SECLEVEL=0"
    with pytest.raises(LockdownError, match="has no host certificate and key"):
        tls_context({"HostCertificate": b"x"})
    with pytest.raises(LockdownError, match="cannot be used"):
        tls_context(PAIR_RECORD, make=lambda: Context(refuse=True))  # type: ignore[arg-type, return-value]
    assert isinstance(tls_context.__kwdefaults__["make"](), ssl.SSLContext)  # type: ignore[index]
