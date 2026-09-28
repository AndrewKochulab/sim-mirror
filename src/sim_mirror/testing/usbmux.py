# SPDX-License-Identifier: Apache-2.0
"""A Mac's usbmuxd and a cabled device's services as tests play them, speaking their real wire formats.

`FakeMuxd` answers usbmuxd's requests -- the devices, a pairing record, a connection to a port -- and, once a port is
connected, hands the socket to what serves that port: `FakeLockdownd` on lockdownd's, answering its requests, or a
`FakeService` that sends bytes, as the device's log relay does. `plain_tls` stands for the device's TLS, so what is
tested is SimMirror's side of the conversation, not OpenSSL's.
"""

from __future__ import annotations

import plistlib
import socket
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sim_mirror.platform.usbmux import HEADER

LOCKDOWN_PORT = 62078
#: The pairing record a trusted Mac keeps for a device, with made-up certificates.
PAIR_RECORD = {
    "HostID": "HOST-ID",
    "SystemBUID": "SYSTEM-BUID",
    "HostCertificate": b"-----BEGIN CERTIFICATE-----\nhost\n-----END CERTIFICATE-----\n",
    "HostPrivateKey": b"-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n",
}


class Port(Protocol):
    """What serves a port on the fake device: it hears what is sent and says what comes back."""

    def heard(self, data: bytes) -> bytes: ...

    def first(self) -> bytes:
        """What the port sends before it is asked anything."""
        ...


@dataclass
class FakeService:
    """A service that only sends: the device's log relay."""

    sends: bytes = b""

    def heard(self, data: bytes) -> bytes:
        return b""

    def first(self) -> bytes:
        return self.sends


@dataclass
class FakeLockdownd:
    """lockdownd: its type, a session, values, and services it starts on ports of `muxd`'s."""

    muxd: FakeMuxd
    values: dict[str, Any] = field(default_factory=lambda: {"ProductVersion": "26.3"})
    services: dict[str, tuple[int, bool]] = field(default_factory=dict)
    kind: str = "com.apple.mobile.lockdown"
    refuse: dict[str, str] = field(default_factory=dict)
    #: Whether the session is wrapped in TLS, as lockdownd asks of every Mac but one it already trusts on USB.
    session_ssl: bool = True
    requests: list[dict[str, Any]] = field(default_factory=list)
    _buffer: bytes = b""

    def first(self) -> bytes:
        return b""

    def heard(self, data: bytes) -> bytes:
        self._buffer += data
        said = b""
        while len(self._buffer) >= 4:
            (length,) = struct.unpack(">I", self._buffer[:4])
            if len(self._buffer) < 4 + length:
                break
            request = plistlib.loads(self._buffer[4 : 4 + length])
            self._buffer = self._buffer[4 + length :]
            self.requests.append(request)
            answer = self.answer(request)
            body = plistlib.dumps(answer)
            said += struct.pack(">I", len(body)) + body
        return said

    def answer(self, request: Mapping[str, Any]) -> dict[str, Any]:
        name = str(request.get("Request"))
        if name in self.refuse:
            return {"Request": name, "Error": self.refuse[name]}
        if name == "QueryType":
            return {"Request": name, "Type": self.kind}
        if name == "StartSession":
            return {"Request": name, "SessionID": "S", "EnableSessionSSL": self.session_ssl}
        if name == "GetValue":
            key = request.get("Key")
            if key not in self.values:
                return {"Request": name, "Key": key, "Error": "MissingValue"}
            return {"Request": name, "Key": key, "Value": self.values[key]}
        if name == "StartService":
            port, secure = self.services.get(str(request.get("Service")), (0, False))
            started = {"Request": name, "Service": request.get("Service"), "EnableServiceSSL": secure}
            return {**started, "Port": port} if port else started
        return {"Request": name, "Error": "UnknownRequest"}


@dataclass
class FakeMuxd:
    """usbmuxd: the devices it reaches, the pairing records it keeps, and what serves each port on them."""

    devices: list[dict[str, Any]] = field(default_factory=list)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    ports: dict[int, Port] = field(default_factory=dict)
    refuse_ports: set[int] = field(default_factory=set)
    sockets: list[FakeMuxSocket] = field(default_factory=list)
    answer_with: Callable[[dict[str, Any]], dict[str, Any] | bytes] | None = None

    def plug(
        self, udid: str, *, device_id: int = 12, connection: str = "USB", record: dict[str, Any] | None = None
    ) -> FakeMuxd:
        """A device plugged in -- usbmuxd names a cabled one without its dash -- that has trusted this Mac."""
        serial = udid.replace("-", "") if connection == "USB" else udid
        self.devices.append(
            {"DeviceID": device_id, "Properties": {"SerialNumber": serial, "ConnectionType": connection}}
        )
        self.records[udid] = PAIR_RECORD if record is None else record
        return self

    def lockdownd(self) -> FakeLockdownd:
        served = FakeLockdownd(self)
        self.ports[LOCKDOWN_PORT] = served
        return served

    def connect(self, path: str, timeout: float) -> FakeMuxSocket:
        sock = FakeMuxSocket(self)
        self.sockets.append(sock)
        return sock

    def answer(self, request: dict[str, Any]) -> dict[str, Any] | bytes:
        if self.answer_with is not None:
            return self.answer_with(request)
        kind = request.get("MessageType")
        if kind == "ListDevices":
            return {"DeviceList": self.devices}
        if kind == "ReadPairRecord":
            record = self.records.get(str(request.get("PairRecordID")))
            return (
                {"PairRecordData": plistlib.dumps(record)}
                if record is not None
                else {"MessageType": "Result", "Number": 2}
            )
        if kind == "Connect":
            port = socket.ntohs(int(request["PortNumber"]))
            ok = port in self.ports and port not in self.refuse_ports
            return {"MessageType": "Result", "Number": 0 if ok else 3, "_port": port}
        return {"MessageType": "Result", "Number": 1}


class FakeMuxSocket:
    """One connection to the fake usbmuxd: requests while it is usbmuxd's, then a port's once connected."""

    def __init__(self, muxd: FakeMuxd) -> None:
        self._muxd = muxd
        self._pending = b""
        self._incoming = b""
        self.port: Port | None = None
        self.closed = False
        self.timeout: float | None = None

    def __enter__(self) -> FakeMuxSocket:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        if self.port is not None:
            self._pending += self.port.heard(data)
            return
        self._incoming += data
        length = HEADER.unpack(self._incoming[: HEADER.size])[0]
        request = plistlib.loads(self._incoming[HEADER.size : length])
        self._incoming = self._incoming[length:]
        answer = self._muxd.answer(request)
        if isinstance(answer, bytes):
            self._pending += answer
            return
        port = answer.pop("_port", None)
        body = plistlib.dumps(answer)
        self._pending += HEADER.pack(HEADER.size + len(body), 1, 8, 1) + body
        if port is not None and answer.get("Number") == 0:
            self.port = self._muxd.ports[port]
            self._pending += self.port.first()

    def recv(self, size: int) -> bytes:
        data, self._pending = self._pending[:size], self._pending[size:]
        return data

    def close(self) -> None:
        self.closed = True


class _PlainContext:
    """A TLS context that wraps nothing: the fake device speaks in the clear."""

    def wrap_socket(self, sock: Any, server_hostname: str | None = None) -> Any:
        sock.wrapped = True
        return sock


def plain_tls(record: Mapping[str, Any]) -> Any:
    return _PlainContext()
