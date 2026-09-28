# SPDX-License-Identifier: Apache-2.0
"""What a real device's cable gives SimMirror, reached through usbmuxd: whether it is plugged in, and its log.

Both are blocking calls on a unix socket; the connector makes them on a thread.
"""

from __future__ import annotations

from typing import Protocol

from sim_mirror.core.device_logs import LogStream
from sim_mirror.platform.lockdown import open_syslog
from sim_mirror.platform.usbmux import Usbmux, UsbmuxError


class Cable(Protocol):
    def cabled(self, udid: str) -> bool:
        """Whether the device is plugged into this Mac now."""
        ...

    def open_log(self, udid: str) -> LogStream:
        """The device's log as it is written. Raises `DeviceControlError` when it cannot be read."""
        ...


class UsbCable:
    """A device's cable, through the Mac's usbmuxd."""

    def __init__(self, usbmux: Usbmux | None = None) -> None:
        self.usbmux = usbmux or Usbmux()

    def cabled(self, udid: str) -> bool:
        try:
            return self.usbmux.find(udid) is not None
        except UsbmuxError:
            return False

    def open_log(self, udid: str) -> LogStream:
        return open_syslog(self.usbmux, udid)
