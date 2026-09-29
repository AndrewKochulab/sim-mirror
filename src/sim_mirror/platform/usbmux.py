# SPDX-License-Identifier: Apache-2.0
"""usbmuxd, the Mac's own service that reaches a cabled iPhone, spoken to directly -- no libimobiledevice.

usbmuxd listens on a unix socket. A request is a 16-byte header -- its length, protocol version 1, message type 8 (a
property list) and a tag, each a little-endian 32-bit count -- and an XML property list. It lists the devices it can
reach, hands over the pairing record the Mac made when the device trusted it, and connects to a TCP port on a device:
once it answers that connection's request with success, the socket carries the device's port itself.

Nothing here pairs a device or changes its trust; a device that has not trusted this Mac has no pairing record, and
reaching it is refused. `USBMUXD_ENV` moves the socket, so tests never reach the real one.
"""

from __future__ import annotations

import os
import plistlib
import socket
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sim_mirror.platform.errors import DeviceControlError

USBMUXD_ENV = "SIM_MIRROR_USBMUXD"
USBMUXD = "/var/run/usbmuxd"
HEADER = struct.Struct("<IIII")
PLIST_VERSION = 1
PLIST_MESSAGE = 8
#: How long a request to usbmuxd may take.
TIMEOUT_S = 5.0
#: The largest answer usbmuxd is trusted to send.
LARGEST_S = 1 << 20
PROGRAM = "sim-mirror"


class UsbmuxError(DeviceControlError):
    """usbmuxd could not be reached, or refused what it was asked."""


@dataclass(frozen=True)
class UsbDevice:
    """A device usbmuxd reaches: its id for this connection to usbmuxd, its UDID, and how -- by ``USB`` or network."""

    device_id: int
    udid: str
    connection: str

    @property
    def cabled(self) -> bool:
        return self.connection == "USB"


def socket_path(env: Mapping[str, str] = os.environ) -> str:
    return env.get(USBMUXD_ENV) or USBMUXD


def same_udid(a: str, b: str) -> bool:
    """Whether two UDIDs are one device's, however each is dashed: usbmuxd names a cabled device without its dash."""
    return a.replace("-", "").upper() == b.replace("-", "").upper()


def encode(message: Mapping[str, Any], tag: int = 1) -> bytes:
    """A request to usbmuxd: its header, then its property list."""
    body = plistlib.dumps({"ClientVersionString": PROGRAM, "ProgName": PROGRAM, **message})
    return HEADER.pack(HEADER.size + len(body), PLIST_VERSION, PLIST_MESSAGE, tag) + body


def recv_exact(sock: socket.socket, count: int) -> bytes:
    """Exactly `count` bytes from a socket; refused when it closes first."""
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise UsbmuxError("the connection closed before its answer was whole")
        data += chunk
    return data


def unix_socket(path: str, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError as exc:
        sock.close()
        raise UsbmuxError(f"usbmuxd cannot be reached at {path}: {exc}") from exc
    return sock


class Usbmux:
    """usbmuxd, one connection per request as it expects."""

    def __init__(
        self,
        path: str | None = None,
        *,
        timeout: float = TIMEOUT_S,
        connect: Callable[[str, float], socket.socket] = unix_socket,
    ) -> None:
        self.path = path or socket_path()
        self._timeout = timeout
        self._connect = connect

    def _ask(self, sock: socket.socket, message: Mapping[str, Any]) -> dict[str, Any]:
        sock.sendall(encode(message))
        length, _version, _kind, _tag = HEADER.unpack(recv_exact(sock, HEADER.size))
        if not HEADER.size <= length <= LARGEST_S:
            raise UsbmuxError(f"usbmuxd answered with a length of {length}")
        try:
            answer = plistlib.loads(recv_exact(sock, length - HEADER.size))
        except plistlib.InvalidFileException as exc:
            raise UsbmuxError("usbmuxd answered with something that is not a property list") from exc
        if not isinstance(answer, dict):
            raise UsbmuxError("usbmuxd answered with something that is not a dictionary")
        return answer

    def request(self, message: Mapping[str, Any]) -> dict[str, Any]:
        with self._connect(self.path, self._timeout) as sock:
            return self._ask(sock, message)

    def devices(self) -> list[UsbDevice]:
        """The devices usbmuxd reaches now, cabled or on the network."""
        answer = self.request({"MessageType": "ListDevices"})
        found = []
        for entry in answer.get("DeviceList") or ():
            properties = entry.get("Properties") if isinstance(entry, dict) else None
            if not isinstance(properties, dict) or not isinstance(entry.get("DeviceID"), int):
                continue
            found.append(
                UsbDevice(
                    entry["DeviceID"],
                    str(properties.get("SerialNumber", "")),
                    str(properties.get("ConnectionType", "")),
                )
            )
        return found

    def find(self, udid: str, *, cabled: bool = True) -> UsbDevice | None:
        """The device with this UDID -- by its cable unless `cabled` is false -- or None."""
        return next(
            (device for device in self.devices() if same_udid(device.udid, udid) and (device.cabled or not cabled)),
            None,
        )

    def pair_record(self, udid: str) -> dict[str, Any]:
        """The pairing record the Mac made when the device trusted it: its certificates, keys and host ids."""
        answer = self.request({"MessageType": "ReadPairRecord", "PairRecordID": udid})
        data = answer.get("PairRecordData")
        if not isinstance(data, bytes):
            raise UsbmuxError(f"this Mac has no pairing record for {udid}: unlock the device and choose Trust")
        record = plistlib.loads(data)
        if not isinstance(record, dict):
            raise UsbmuxError(f"the pairing record for {udid} is not a dictionary")
        return record

    def connect(self, device_id: int, port: int) -> socket.socket:
        """A socket to a TCP port on the device: once this returns, what is sent reaches the device's port."""
        if not 0 < port < 65536:
            raise UsbmuxError(f"not a port: {port}")
        sock = self._connect(self.path, self._timeout)
        try:
            answer = self._ask(
                sock, {"MessageType": "Connect", "DeviceID": device_id, "PortNumber": socket.htons(port)}
            )
            if answer.get("MessageType") != "Result" or answer.get("Number") != 0:
                raise UsbmuxError(f"the device's port {port} did not answer (usbmuxd said {answer.get('Number')})")
        except BaseException:
            sock.close()
            raise
        return sock
