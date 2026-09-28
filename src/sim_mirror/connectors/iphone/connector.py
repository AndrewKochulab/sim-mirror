# SPDX-License-Identifier: Apache-2.0
"""The iphone connector: a real iPhone or iPad connected to the Mac.

It is the only connector a real device has, and it chooses how to reach the device each time it attaches, from what
is there now: the screen by screenshot through devicectl, with no cable and nothing installed. What a session can do
is what the device says it can -- devicectl lists each device's features -- so a device that cannot, say, simulate a
place is never offered it. Its note says what would let it do more.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import (
    Capability,
    ConnectorReport,
    ConnectorUnavailable,
    DeviceSession,
)
from sim_mirror.connectors.iphone.screen import FPS_LIMIT, DevicectlScreen
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl.connector import NO_XCRUN
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, PhysicalDevice
from sim_mirror.platform.xcrun import xcrun_binary
from sim_mirror.protocol import DeviceKind

NAME = "iphone"
KINDS: frozenset[DeviceKind] = frozenset({"physical"})
#: What a device can be offered by what it says it can do, by devicectl's feature names.
FEATURES: Mapping[str, frozenset[Capability]] = {
    "capturescreenshot": frozenset({Capability.SCREENSHOT, Capability.STREAM_JPEG, Capability.RECORD}),
    "installapp": frozenset({Capability.APP_INSTALL}),
    "launchapplication": frozenset({Capability.APP_LAUNCH, Capability.OPEN_URL}),
    "customizeuistyle": frozenset({Capability.APPEARANCE}),
    "customizeappearancesettings": frozenset({Capability.ACCESSIBILITY}),
    "simulatelocation": frozenset({Capability.LOCATION}),
    "simulateStatusBar": frozenset({Capability.STATUS_BAR}),
}
#: What any real device is offered: it is listed, picked and brought up.
ALWAYS = frozenset({Capability.LIFECYCLE, Capability.DEVICE_LIST})
#: The most a real device can be offered, as the connector reports it before it knows the device.
MOST = frozenset(Capability) - {Capability.BUILD_PREVIEW}


def capabilities_of(device: PhysicalDevice) -> frozenset[Capability]:
    """What this device can be offered through devicectl alone, by the features it says it has."""
    offered = set(ALWAYS)
    for feature, capabilities in FEATURES.items():
        if feature in device.features:
            offered |= capabilities
    return frozenset(offered)


class IPhoneConnector:
    """Reaches a real device through devicectl."""

    name = NAME
    #: The kinds of device it drives, known before it is probed.
    kinds = KINDS

    def __init__(
        self,
        devicectl_for: Callable[[str], Devicectl],
        *,
        copy: HostCopy | None = None,
        has_xcrun: Callable[[], bool] = lambda: xcrun_binary() is not None,
    ) -> None:
        self._devicectl_for = devicectl_for
        self._copy = copy or HostCopy()
        self._has_xcrun = has_xcrun

    @staticmethod
    def _xcode(config: SimConfig) -> str:
        return config.real_devices_developer_dir or config.developer_dir

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not config.real_devices:
            return ConnectorReport(NAME, False, reasons=(self._copy.kind_unavailable("physical"),), kinds=KINDS)
        if not self._has_xcrun():
            return ConnectorReport(NAME, False, reasons=(NO_XCRUN,), kinds=KINDS)
        return ConnectorReport(NAME, True, MOST, kinds=KINDS)

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        devicectl = self._devicectl_for(self._xcode(config))
        try:
            device = await devicectl.device(udid)
            if device is None or not device.connected:
                raise ConnectorUnavailable(self._copy.not_connected(device.name if device else udid), 409)
            display = await devicectl.display(udid)
        except DevicectlError as exc:
            raise ConnectorUnavailable(str(exc)) from exc
        return DeviceSession(
            connector=NAME,
            capabilities=capabilities_of(device),
            screen=DevicectlScreen(devicectl, udid, display),
            fps_limit=FPS_LIMIT,
            note=self._copy.iphone_limits(),
        )

    async def reap_orphans(self) -> int:
        return 0


def create(context: ConnectorContext) -> IPhoneConnector:
    return IPhoneConnector(context.devicectl_for, copy=context.copy)
