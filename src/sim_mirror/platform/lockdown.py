# SPDX-License-Identifier: Apache-2.0
"""A cabled device's lockdownd, reached through usbmuxd with the Mac's own pairing record.

lockdownd listens on the device's port 62078 and speaks property lists, each after its length as a big-endian 32-bit
count. SimMirror opens a session with the host id and system BUID the pairing record holds, which lockdownd then
wraps in TLS with the record's host certificate; in that session it reads a value, or starts a service -- the device's
log, ``com.apple.syslog_relay`` -- and connects to the port lockdownd names, wrapped in TLS too when it says so.

It only reads: nothing here pairs, unpairs, installs a profile or changes a setting. The pairing record's key is
written for TLS to a folder only this user can read, for as long as the context takes to load it.
"""

from __future__ import annotations

import os
import plistlib
import socket
import ssl
import struct
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.usbmux import Usbmux, UsbmuxError, recv_exact

LOCKDOWN_PORT = 62078
LENGTH = struct.Struct(">I")
#: The largest message lockdownd is trusted to send.
LARGEST = 1 << 20
LABEL = "sim-mirror"
#: The device's log, line by line.
SYSLOG_RELAY = "com.apple.syslog_relay"


class LockdownError(DeviceControlError):
    """lockdownd could not be reached, or refused what it was asked."""


def send(sock: socket.socket, message: Mapping[str, Any]) -> None:
    body = plistlib.dumps({"Label": LABEL, **message})
    sock.sendall(LENGTH.pack(len(body)) + body)


def receive(sock: socket.socket) -> dict[str, Any]:
    (length,) = LENGTH.unpack(recv_exact(sock, LENGTH.size))
    if not 0 < length <= LARGEST:
        raise LockdownError(f"lockdownd answered with a length of {length}")
    try:
        answer = plistlib.loads(recv_exact(sock, length))
    except plistlib.InvalidFileException as exc:
        raise LockdownError("lockdownd answered with something that is not a property list") from exc
    if not isinstance(answer, dict):
        raise LockdownError("lockdownd answered with something that is not a dictionary")
    return answer


def ask(sock: socket.socket, message: Mapping[str, Any]) -> dict[str, Any]:
    """A request and its answer; refused with what lockdownd said when it answers with an error."""
    send(sock, message)
    answer = receive(sock)
    if "Error" in answer:
        raise LockdownError(f"lockdownd refused {message.get('Request')}: {answer['Error']}")
    return answer


def tls_context(
    record: Mapping[str, Any], *, make: Callable[[], ssl.SSLContext] = lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
) -> ssl.SSLContext:
    """TLS as the device expects it: this Mac's certificate and key from the pairing record, the device's own
    certificate not checked against a CA -- the pairing is what trusts it -- and the older ciphers it may speak."""
    certificate, key = record.get("HostCertificate"), record.get("HostPrivateKey")
    if not isinstance(certificate, bytes) or not isinstance(key, bytes):
        raise LockdownError("the pairing record has no host certificate and key")
    context = make()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_ciphers("ALL:@SECLEVEL=0")
    with tempfile.TemporaryDirectory(prefix="sim-mirror-pair-") as folder:
        os.chmod(folder, 0o700)
        paths = []
        for name, data in (("host.pem", certificate), ("key.pem", key)):
            path = Path(folder) / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as out:
                out.write(data)
            paths.append(path)
        try:
            context.load_cert_chain(paths[0], paths[1])
        except ssl.SSLError as exc:
            raise LockdownError(f"the pairing record's certificate cannot be used: {exc}") from exc
    return context


class Lockdown:
    """A session with one cabled device's lockdownd."""

    def __init__(
        self,
        usbmux: Usbmux,
        udid: str,
        *,
        context_for: Callable[[Mapping[str, Any]], ssl.SSLContext] = tls_context,
    ) -> None:
        self._usbmux = usbmux
        self._udid = udid
        self._context_for = context_for
        self._sock: socket.socket | None = None
        self._device_id = 0
        self._context: ssl.SSLContext | None = None

    def open(self) -> Lockdown:
        """Reach the device's lockdownd by its cable and start a session. Refused with what to do when it cannot."""
        try:
            device = self._usbmux.find(self._udid)
            if device is None:
                raise LockdownError(f"{self._udid} is not connected by cable")
            record = self._usbmux.pair_record(self._udid)
            sock = self._usbmux.connect(device.device_id, LOCKDOWN_PORT)
        except UsbmuxError as exc:
            raise LockdownError(str(exc)) from exc
        try:
            kind = ask(sock, {"Request": "QueryType"}).get("Type")
            if kind != "com.apple.mobile.lockdown":
                raise LockdownError(f"the device's port {LOCKDOWN_PORT} is not lockdownd ({kind})")
            started = ask(
                sock,
                {"Request": "StartSession", "HostID": record.get("HostID"), "SystemBUID": record.get("SystemBUID")},
            )
            self._context = self._context_for(record)
            if started.get("EnableSessionSSL"):
                sock = self._context.wrap_socket(sock, server_hostname=None)
        except LockdownError:
            sock.close()
            raise
        except (OSError, UsbmuxError) as exc:
            sock.close()
            raise LockdownError(f"the device's lockdownd could not be reached: {exc}") from exc
        self._sock, self._device_id = sock, device.device_id
        return self

    def __enter__(self) -> Lockdown:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _session(self) -> socket.socket:
        if self._sock is None:
            raise LockdownError("the lockdown session is not open")
        return self._sock

    def value(self, key: str, domain: str | None = None) -> Any:
        """A value lockdownd keeps, such as ``ProductVersion``; None when the device has none by that name."""
        request: dict[str, Any] = {"Request": "GetValue", "Key": key}
        if domain is not None:
            request["Domain"] = domain
        send(self._session(), request)
        answer = receive(self._session())
        return None if answer.get("Error") == "MissingValue" else answer.get("Value")

    def start_service(self, name: str) -> socket.socket:
        """A socket to one of the device's services, started by lockdownd, wrapped in TLS when the service wants it."""
        started = ask(self._session(), {"Request": "StartService", "Service": name})
        port = started.get("Port")
        if not isinstance(port, int):
            raise LockdownError(f"lockdownd started {name} on no port")
        try:
            sock = self._usbmux.connect(self._device_id, port)
        except UsbmuxError as exc:
            raise LockdownError(str(exc)) from exc
        if started.get("EnableServiceSSL") and self._context is not None:
            try:
                return self._context.wrap_socket(sock, server_hostname=None)
            except (OSError, ssl.SSLError) as exc:
                sock.close()
                raise LockdownError(f"{name} could not be reached securely: {exc}") from exc
        return sock


def open_syslog(
    usbmux: Usbmux, udid: str, *, context_for: Callable[[Mapping[str, Any]], ssl.SSLContext] = tls_context
) -> socket.socket:
    """The device's log as it is written: its syslog relay, started through a lockdown session that then ends."""
    with Lockdown(usbmux, udid, context_for=context_for) as session:
        return session.start_service(SYSLOG_RELAY)
